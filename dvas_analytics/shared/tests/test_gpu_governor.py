"""Unit tests for the GPU profile, newest-frame sampler and FPS governor
(RunPod plan 9.2 / 9.10 / 10). No GPU, torch or Valkey needed.

Run from dvas_analytics/:  python -m pytest shared/tests/test_gpu_governor.py -q
"""
import asyncio
import json

import pytest

from gateway.governor import (
    DOWN_AFTER, GOVERNED, Governor, PairState, Policy, decide, lagging_pairs,
)
from shared import gpu_profile
from shared.frame_sampler import FrameSampler, RateCap, frame_bytes


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ── GPU profile ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,vram,cc,tier,batch,fp16", [
    ("NVIDIA GeForce RTX 3050 Laptop GPU", 4.0, (8, 6), "small", 1, True),
    ("NVIDIA L40S", 44.5, (8, 9), "large", 4, True),
    ("NVIDIA RTX PRO 6000 Blackwell Server Edition", 95.6, (12, 0), "xlarge", 8, True),
    ("Tesla P4", 7.4, (6, 1), "small", 1, False),
    ("NVIDIA GeForce GTX 1650", 3.6, (7, 5), "small", 1, False),
])
def test_profile_tiers(monkeypatch, name, vram, cc, tier, batch, fp16):
    for k in ("DVAS_FP16", "DVAS_MAX_BATCH", "DVAS_CUDNN_BENCHMARK"):
        monkeypatch.delenv(k, raising=False)
    p = gpu_profile.build_profile(name, vram, cc, 100)
    assert (p.tier, p.max_batch, p.fp16) == (tier, batch, fp16)


def test_profile_env_overrides(monkeypatch):
    monkeypatch.setenv("DVAS_FP16", "false")
    monkeypatch.setenv("DVAS_MAX_BATCH", "999")
    p = gpu_profile.build_profile("NVIDIA L40S", 44.5, (8, 9), 142)
    assert p.fp16 is False and p.max_batch == 32       # clamped
    monkeypatch.setenv("DVAS_MAX_BATCH", "abc")
    assert gpu_profile.build_profile("NVIDIA L40S", 44.5, (8, 9), 142).max_batch == 4


# ── Rate cap ──────────────────────────────────────────────────────────────────

def test_rate_cap_bounds_and_due():
    clock = FakeClock()
    cap = RateCap(1000, clock)
    assert cap.fps == 60                                # clamped to CAP_MAX
    cap.fps = 10
    assert cap.due("D1")
    cap.mark("D1")
    clock.t += 0.05
    assert not cap.due("D1") and cap.wait_s(["D1"]) > 0
    clock.t += 0.05
    assert cap.due("D1")


def test_frame_bytes_prefers_raw_then_b64():
    assert frame_bytes({"frame": b"\xff\xd8raw"}) == b"\xff\xd8raw"
    assert frame_bytes({"jpeg_b64": "aGk="}) == b"hi"
    assert frame_bytes({"jpeg_b64": "!!!"}) is None
    assert frame_bytes({}) is None


# ── Sampler (fake Valkey) ─────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self, streams):
        self.streams = streams          # {key: [(id, fields)]}
        self.pos = {k: 0 for k in streams}
        self.acked = []
        self.kv = {}

    async def xgroup_create(self, key, group, id="0", mkstream=False):
        raise Exception("BUSYGROUP Consumer Group name already exists")

    async def xreadgroup(self, groupname, consumername, streams, count, block):
        out = []
        for key in streams:
            entries = self.streams.get(key, [])
            i = self.pos.get(key, 0)
            part = entries[i:i + count]
            self.pos[key] = i + len(part)
            if part:
                out.append((key.encode(), part))
        return out

    async def xack(self, key, group, *ids):
        self.acked.extend(ids)

    async def xrevrange(self, key, max="+", min="-", count=1):
        return self.streams.get(key, [])[-count:][::-1]

    async def xgroup_setid(self, key, group, eid):
        self.pos[key] = len(self.streams[key])
        self.setid = (key, eid)

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value


def _entries(n):
    return [(f"{i}-0".encode(), {b"frame": b"jpg%d" % i, b"ts": b"0", b"frame_seq": str(i).encode()})
            for i in range(1, n + 1)]


def test_sampler_returns_newest_and_acks_backlog():
    clock = FakeClock()
    fake = FakeRedis({"drone:frames:D1": _entries(70), "drone:frames:D2": _entries(1)})
    s = FrameSampler("vehicle", "g", "c", redis_factory=lambda: fake, clock=clock)
    got = asyncio.run(s.next_round(["D1", "D2"]))
    assert got["D1"]["frame"] == b"jpg70" and got["D1"]["entry_id"] == "70-0"
    assert got["D2"]["frame"] == b"jpg1"
    # backlog > one chunk: only one chunk is transferred, then the group jumps to the newest
    assert len(fake.acked) == 33 and fake.setid == ("drone:frames:D1", "70-0")
    assert s.stats["D1"].skipped == 69 and s.stats["D2"].skipped == 0
    assert len(s.stats["D1"].received) == 1          # backlog is not counted as input rate


def test_sampler_follows_governor_cap_and_rejects_bad_values():
    clock = FakeClock()
    fake = FakeRedis({})
    s = FrameSampler("person", "g", "c", redis_factory=lambda: fake, clock=clock)
    fake.kv["dvas:fps_cap:person"] = json.dumps({"cap": 12.0})
    asyncio.run(s._refresh_cap())
    assert s.cap.fps == 12.0 and s.cap_source == "governor"
    clock.t += 5
    fake.kv["dvas:fps_cap:person"] = json.dumps({"cap": 0.0001})   # out of bounds → ignored
    asyncio.run(s._refresh_cap())
    assert s.cap_source == "env"


def test_sampler_stats_snapshot():
    clock = FakeClock()
    s = FrameSampler("vehicle", "g", "c", redis_factory=lambda: FakeRedis({}), clock=clock)
    for _ in range(50):
        s.record("D1", infer_ms=10, total_ms=30)
        clock.t += 0.04                                  # 25 fps
    snap = s.snapshot()
    assert 23 <= snap["drones"]["D1"]["fps"] <= 26
    assert snap["drones"]["D1"]["infer_ms"] == 10.0


# ── Governor ──────────────────────────────────────────────────────────────────

def _pair(svc, fps, inp=30.0, drone="D1"):
    return PairState(svc, drone, fps, inp, 0, 10.0, 50.0)


def test_policy_clamps_and_parses():
    p = Policy(40, 25, 50).clamped()
    assert p.target_max == 25 and p.target_min == 25 and p.floor == 25
    assert Policy.parse('{"target_min": 20, "target_max": 30, "floor": 5}') == Policy(20, 30, 5)
    assert Policy.parse('{"target_min": "x"}') is None
    assert Policy.parse(b"not json") is None


def test_overload_lowers_common_cap_after_hysteresis_but_not_below_floor():
    pol = Policy(20, 30, 5)
    pairs = [_pair("person", 12), _pair("vehicle", 29)]
    d, n = decide(30, pol, pairs, gpu_pct=98, overloaded_cycles=0)
    assert d.state == "overloaded" and d.cap == 30 and n == 1       # first cycle: hold
    d, n = decide(30, pol, pairs, gpu_pct=98, overloaded_cycles=n)
    assert d.cap < 30 and n == DOWN_AFTER
    cap = d.cap
    for _ in range(30):
        d, n = decide(cap, pol, [_pair("person", 1)], gpu_pct=99, overloaded_cycles=n)
        cap = d.cap
    assert cap == 5 and "floor" in d.reason


def test_headroom_raises_cap_to_target_max():
    pol = Policy(20, 30, 5)
    d, _ = decide(10, pol, [_pair("person", 10)], gpu_pct=40, overloaded_cycles=3)
    assert d.cap == 12 and d.state == "ok"
    d, _ = decide(29.5, pol, [_pair("person", 29)], gpu_pct=40, overloaded_cycles=0)
    assert d.cap == 30


def test_cpu_bound_shortfall_is_not_gpu_overload():
    d, n = decide(30, Policy(), [_pair("anpr", 8)], gpu_pct=35, overloaded_cycles=0)
    assert d.state == "ok" and "CPU/IO" in d.reason and n == 0


def test_input_limited_is_reported_and_crowd_ignored():
    pol = Policy(20, 30, 5)
    d, _ = decide(30, pol, [_pair("vehicle", 10, inp=10)], gpu_pct=30, overloaded_cycles=0)
    assert d.state == "input_limited" and "10 fps" in d.reason
    # crowd alone (ungoverned, 2 fps) never drives the governor
    assert "crowd" not in GOVERNED
    assert lagging_pairs([_pair("crowd", 2)], 30) == []
    d, _ = decide(30, pol, [_pair("crowd", 2)], gpu_pct=30, overloaded_cycles=0)
    assert d.state == "idle"


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def set(self, k, v, ex=None):
        self.ops.append((k, v))

    async def execute(self):
        for k, v in self.ops:
            self.r.kv[k] = v


class GovRedis:
    def __init__(self):
        self.kv = {}

    async def get(self, k):
        return self.kv.get(k)

    async def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    async def set(self, k, v, ex=None):
        self.kv[k] = v

    def pipeline(self):
        return FakePipe(self)


def test_governor_cycle_writes_caps_and_status():
    r = GovRedis()
    r.kv["dvas:fps_policy"] = json.dumps({"target_min": 15, "target_max": 25, "floor": 4})
    r.kv["dvas:svc_stats:vehicle"] = json.dumps({
        "gpu": {"name": "NVIDIA L40S", "tier": "large"}, "cap_fps": 30,
        "drones": {"D1": {"fps": 24.5, "input_fps": 25, "skipped": 0}}})
    gov = Governor(lambda: r, gpu_reader=lambda: 50.0)
    status = asyncio.run(gov.cycle())
    assert status["policy"]["target_max"] == 25 and status["cap_fps"] == 25
    assert json.loads(r.kv["dvas:fps_cap:person"])["cap"] == 25
    assert status["gpu"]["name"] == "NVIDIA L40S"
    assert "dvas:fps_cap:crowd" not in r.kv


def test_unknown_gpu_holds_cap_instead_of_throttling():
    d, n = decide(22, Policy(20, 30, 5), [_pair("anpr", 4)], gpu_pct=None, overloaded_cycles=1)
    assert d.cap == 22 and d.state == "ok" and "unknown" in d.reason and n == 0
