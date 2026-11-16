"""GPU/CPU stage pipelining (v2 plan R4.2): same results, rounds in order, ack after finish.

Part 1 (always): the pipeline with a fake sampler and fake stages.
Part 2 (needs the models and a clip, e.g. on the pod or in the pod image):
    DVAS_TEST_CLIP=/path/clip.mp4 python -m pytest shared/tests/test_stage_pipeline.py -q
runs the real vehicle, ANPR and person analyzers on 2 drones, once round by round
(analyze_batch) and once pipelined, and requires identical results.

Run from dvas_analytics/.
"""
import asyncio
import os
import threading
import time

import pytest

from shared import stage_pipeline as sp


class FakeSampler:
    """Rounds from a list; records what was acknowledged and when."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.acked, self._returned = [], []

    async def _take(self):
        if not self.rounds:
            return {}, []
        r = self.rounds.pop(0)
        return r, [("s", f"{d}-{f['seq']}") for d, (f, _fr) in r.items()]

    async def next_decoded(self, drones):
        self.acked += self._returned
        got, self._returned = await self._take()
        return got

    async def next_decoded_held(self, drones):
        self.acked += self._returned
        self._returned = []
        return await self._take()

    async def ack_held(self, token):
        self.acked += list(token or [])

    async def done(self):
        self.acked += self._returned
        self._returned = []


def _rounds(n, drones=("A", "B")):
    return [{d: ({"seq": i}, f"{d}{i}") for d in drones} for i in range(n)]


def _drive(rounds, enabled, monkeypatch, stop_after=None):
    monkeypatch.setattr(sp, "ENABLED", enabled)
    sampler = FakeSampler(rounds)
    log, lock = [], threading.Lock()

    def infer(frames):
        with lock:
            log.append(("infer", tuple(sorted(frames.values())), time.monotonic()))
        time.sleep(0.01)
        return {d: fr.upper() for d, fr in frames.items()}

    def finish(frames, handle):
        with lock:
            log.append(("finish", tuple(sorted(frames.values())), tuple(sampler.acked), time.monotonic()))
        time.sleep(0.02)
        with lock:
            log.append(("finished", tuple(sorted(frames.values())), time.monotonic()))
        return {d: handle[d] + "!" for d in frames}

    pipe = sp.StagePipeline(sampler, infer)

    async def loop():
        out, k = [], 0
        while True:
            decoded = await pipe.next(["A", "B"])
            if not decoded:
                break
            out.append(await pipe.finish({d: fr for d, (_f, fr) in decoded.items()}, finish, ["A", "B"]))
            k += 1
            if stop_after and k == stop_after:
                break
        await pipe.stop()
        await sampler.done()
        return out

    return asyncio.run(loop()), sampler, log, pipe


def test_pipelined_results_equal_round_by_round(monkeypatch):
    plain, s1, _, _ = _drive(_rounds(6), False, monkeypatch)
    piped, s2, log, _ = _drive(_rounds(6), True, monkeypatch)
    assert piped == plain and len(piped) == 6
    assert sorted(s1.acked) == sorted(s2.acked)            # every frame acknowledged once
    assert len(s2.acked) == len(set(s2.acked)) == 12
    finishes = [e[1] for e in log if e[0] == "finish"]
    assert finishes == [(f"A{i}", f"B{i}") for i in range(6)]   # tracker steps in order


def test_round_is_acknowledged_only_after_its_finish(monkeypatch):
    _, _, log, _ = _drive(_rounds(4), True, monkeypatch)
    for i, e in enumerate(e for e in log if e[0] == "finish"):
        # while round i is finished, rounds < i are acknowledged and round i is not
        assert ("s", f"A-{i}") not in e[2] and ("s", f"B-{i}") not in e[2]
        assert all(("s", f"{d}-{j}") in e[2] for d in "AB" for j in range(i))


def test_next_model_call_overlaps_this_rounds_finish(monkeypatch):
    _, _, log, _ = _drive(_rounds(3), True, monkeypatch)
    at = {(e[0], e[1][0]): e[-1] for e in log}
    # round 1's model call starts while round 0's tracker step / post-processing runs
    assert at[("infer", "A1")] < at[("finished", "A0")]


def test_stop_keeps_the_read_ahead_round_unacknowledged(monkeypatch):
    rounds = _rounds(5)
    _out, sampler, _, pipe = _drive(rounds, True, monkeypatch, stop_after=2)
    assert ("s", "A-2") not in sampler.acked                  # read ahead, not finished
    assert pipe._ahead is not None and pipe._ahead[0]["A"][1] == "A2"


# ── Part 2: real analyzers ────────────────────────────────────────────────────

CLIP = os.getenv("DVAS_TEST_CLIP")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
real = pytest.mark.skipif(not CLIP, reason="DVAS_TEST_CLIP not set")
# Wall-clock fields differ between any two runs, pipelined or not: timings, timestamps, and
# vehicle speed (computed from processing time, time.time(), not the frames' capture times)
VOLATILE = {"ts", "inference_ms", "model_ms", "elapsed_ms", "processing_ms", "postprocess_ms",
            "face_ms", "detect_ms", "track_ms", "frame_timestamp", "speed_info"}


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items() if k not in VOLATILE and not k.endswith("_ms")}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


ID_KEYS = ("tracker_id", "track_id")


def _canon_ids(results):
    """Track ids renamed per drone in order of first appearance: the numbers come from one
    counter shared by drones tracked in parallel threads, so they differ between any two
    runs (shared/tests/test_parallel_tracking.py); which object has which track must not."""
    names = {}

    def walk(x, d):
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k in ID_KEYS and isinstance(v, int) and v > 0:
                    v = names.setdefault((d, v), f"{d}#{sum(1 for n in names if n[0] == d)}")
                elif k == "recognitions" and isinstance(v, dict):
                    v = {str(names.setdefault((d, int(t)), f"{d}#{sum(1 for n in names if n[0] == d)}")): walk(r, d)
                         for t, r in v.items()}
                    out[k] = v
                    continue
                out[k] = walk(v, d)
            return out
        if isinstance(x, list):
            return [walk(v, d) for v in x]
        return x
    return [{d: walk(r, d) for d, r in rnd.items()} for rnd in results]


def _first_diff(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}/{k}: missing on one side"
            d = _first_diff(a[k], b[k], f"{path}/{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} vs {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {str(a)[:80]} vs {str(b)[:80]}"


def _clip_rounds(n, stride=3):
    import cv2  # noqa: PLC0415
    cap = cv2.VideoCapture(CLIP)
    frames, k = [], 0
    while len(frames) < 2 * n:
        ok, f = cap.read()
        if not ok:
            break
        if k % stride == 0:
            frames.append(f)
        k += 1
    half = len(frames) // 2
    # drone B flies the second half of the clip (different content, same size)
    return [{"A": ({"seq": i}, frames[i]), "B": ({"seq": i}, frames[half + i])}
            for i in range(min(n, half))]


def _compare(make, finish_kw, monkeypatch, n=40, piped_side=True):
    import torch  # noqa: PLC0415
    from ultralytics.trackers.basetrack import BaseTrack  # noqa: PLC0415
    # cuDNN autotune (gpu_profile turns it on) picks convolution algorithms by timing them, so
    # two model instances can differ in the last FP16 digits whatever the loop does: measured
    # 16 Nov in the full suite, two NON-pipelined runs differed exactly like a pipelined one
    # (confidence 0.5758 vs 0.5757). Fixed algorithms here, so only the pipeline is compared.
    monkeypatch.setattr(torch.backends.cudnn, "benchmark", False)
    monkeypatch.setattr(torch.backends.cudnn, "deterministic", True)
    rounds = _clip_rounds(n)
    BaseTrack.reset_id()
    a = make()
    plain = [a.analyze_batch({d: fr for d, (_f, fr) in r.items()}, **finish_kw) for r in rounds]
    BaseTrack.reset_id()
    b = make()
    monkeypatch.setattr(sp, "ENABLED", piped_side)
    sampler = FakeSampler(rounds)
    pipe = sp.StagePipeline(sampler, b.infer_batch)

    async def loop():
        out = []
        while True:
            decoded = await pipe.next(["A", "B"])
            if not decoded:
                return out
            out.append(await pipe.finish({d: fr for d, (_f, fr) in decoded.items()},
                                         lambda fr, h: b.finish_batch(fr, h, **finish_kw), ["A", "B"]))

    piped = asyncio.run(loop())
    assert len(piped) == len(plain) == len(rounds)
    plain, piped = _canon_ids(_clean(plain)), _canon_ids(_clean(piped))
    for i, (x, y) in enumerate(zip(plain, piped)):
        assert x == y, f"round {i} differs: {_first_diff(x, y)}"
    return sum(len(str(r)) for r in plain)


@real
def test_vehicle_identical(monkeypatch):
    from vehicle.analyzer import VehicleAnalyzer  # noqa: PLC0415
    w = os.path.join(ROOT, "vehicle/models/visdrone.pt")
    assert _compare(lambda: VehicleAnalyzer(w), {"save_crops": True}, monkeypatch) > 1000


@real
def test_anpr_identical(monkeypatch):
    from anpr.analyzer import ANPRAnalyzer  # noqa: PLC0415
    m = os.path.join(ROOT, "anpr/models")
    make = lambda: ANPRAnalyzer(vehicle_model_path=f"{m}/visdrone.pt",  # noqa: E731
                                lp_model_path=f"{m}/license_plate_detector.pt")
    assert _compare(make, {}, monkeypatch) > 1000


@real
def test_person_identical(monkeypatch):
    from person.analyzer import PersonFaceAnalyzer  # noqa: PLC0415
    m = os.path.join(ROOT, "person/models")
    make = lambda: PersonFaceAnalyzer(f"{m}/yolov8m.pt", f"{m}/yolov8n-face.pt")  # noqa: E731
    assert _compare(make, {"save_crops": True}, monkeypatch) > 1000

