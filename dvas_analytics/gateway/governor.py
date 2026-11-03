# -*- coding: utf-8 -*-
"""
DVAS Analytics — FPS governor (RunPod plan Phase 10, decision: every enabled
analyzer except crowd aims for the target range; under GPU overload they all
slow down equally, never below the floor).

Every 2 s the gateway:
  1. reads `dvas:svc_stats:<service>` (published by each analyzer's FrameSampler)
     and GPU utilisation (NVML),
  2. moves one common cap between `floor` and `target_max`:
       - some analyzer can't reach its cap (and isn't limited by its input) while
         the GPU is ≥ GPU_BUSY_PCT busy  → step the cap down
       - otherwise, with headroom       → step the cap back up
  3. writes `dvas:fps_cap:<service>` (TTL 10 s) for every governed service and a
     status document `dvas:governor:status` for the Detection Models page.

If the gateway stops, the caps expire and analyzers fall back to DVAS_FPS_MAX.
The policy (target_min / target_max / floor) lives in `dvas:fps_policy`,
written by the dashboard backend (SO/SA, Detection Models page).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("analytics-gateway.governor")

POLICY_KEY = "dvas:fps_policy"
STATUS_KEY = "dvas:governor:status"
CAP_KEY = "dvas:fps_cap:{service}"
STATS_KEY = "dvas:svc_stats:{service}"
CAP_TTL_S = 10
STATUS_TTL_S = 15
CYCLE_S = 2.0

GOVERNED = ("person", "vehicle", "anpr", "intrusion", "vessel", "anomaly")
UNGOVERNED = ("crowd",)          # counts need 1–2 fps (CROWD_MAX_FPS)
ALL_SERVICES = GOVERNED + UNGOVERNED

GPU_BUSY_PCT = 90.0      # above this the GPU is the bottleneck
GPU_IDLE_PCT = 80.0      # below this there is room to raise the cap
SHORTFALL = 0.85         # an analyzer "can't keep up" below 85 % of min(cap, input)
STEP_DOWN = 0.85         # multiplicative decrease
STEP_UP = 2.0            # additive increase (fps per cycle)
DOWN_AFTER = 2           # consecutive overloaded cycles before lowering

# Absolute bounds for any policy value (validated again by the backend)
FPS_LIMIT_MIN, FPS_LIMIT_MAX = 1, 60


@dataclass
class Policy:
    target_min: float = 20.0
    target_max: float = 30.0
    floor: float = 5.0

    @classmethod
    def from_env(cls) -> "Policy":
        def f(name, default):
            try:
                return float(os.getenv(name, default))
            except ValueError:
                return float(default)
        return cls(f("DVAS_FPS_MIN", 20), f("DVAS_FPS_MAX", 30), f("DVAS_FPS_FLOOR", 5)).clamped()

    def clamped(self) -> "Policy":
        mx = max(FPS_LIMIT_MIN, min(FPS_LIMIT_MAX, self.target_max))
        mn = max(FPS_LIMIT_MIN, min(mx, self.target_min))
        fl = max(FPS_LIMIT_MIN, min(mn, self.floor))
        return Policy(mn, mx, fl)

    @classmethod
    def parse(cls, raw) -> Optional["Policy"]:
        try:
            d = json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
            return cls(float(d["target_min"]), float(d["target_max"]), float(d["floor"])).clamped()
        except (TypeError, ValueError, KeyError):
            return None


@dataclass
class PairState:
    service: str
    drone_id: str
    fps: float
    input_fps: float
    skipped: int
    infer_ms: Optional[float]
    lag_ms: Optional[float]


@dataclass
class Decision:
    cap: float
    state: str                    # ok | overloaded | input_limited | idle
    reason: str
    lagging: List[str] = field(default_factory=list)


def pairs_from_stats(stats: Dict[str, dict]) -> List[PairState]:
    out = []
    for svc, doc in stats.items():
        for d, s in (doc.get("drones") or {}).items():
            out.append(PairState(svc, d, float(s.get("fps") or 0), float(s.get("input_fps") or 0),
                                 int(s.get("skipped") or 0), s.get("infer_ms"), s.get("lag_ms")))
    return out


def lagging_pairs(pairs: List[PairState], cap: float) -> List[PairState]:
    """Governed pairs that get fewer frames analysed than they could."""
    out = []
    for p in pairs:
        if p.service not in GOVERNED or p.input_fps < 0.5:
            continue
        expected = min(cap, p.input_fps)
        if p.fps < SHORTFALL * expected:
            out.append(p)
    return out


def decide(cap: float, policy: Policy, pairs: List[PairState], gpu_pct: Optional[float],
           overloaded_cycles: int) -> tuple:
    """One governor step. Returns (Decision, new overloaded_cycles). Pure; unit-tested."""
    governed = [p for p in pairs if p.service in GOVERNED]
    if not governed:
        return Decision(policy.target_max, "idle", "No governed analyzer is streaming"), 0

    lagging = lagging_pairs(governed, cap)
    if gpu_pct is None:
        # Without NVML a shortfall can't be told apart from CPU/IO limits: hold the cap
        # rather than slow every analyzer down because one is CPU-bound
        names = sorted({f"{p.service}/{p.drone_id}" for p in lagging})
        reason = "GPU utilisation unknown (NVML unavailable) — cap held"
        if names:
            reason += f"; {', '.join(names[:4])} below target"
        return Decision(cap, "ok", reason, names), 0
    gpu_busy = gpu_pct >= GPU_BUSY_PCT
    pressure = bool(lagging) and gpu_busy

    if pressure:
        overloaded_cycles += 1
        if overloaded_cycles >= DOWN_AFTER and cap > policy.floor:
            cap = max(policy.floor, round(cap * STEP_DOWN, 1))
        names = sorted({f"{p.service}/{p.drone_id}" for p in lagging})
        state = "overloaded"
        gpu_txt = f"GPU {gpu_pct:.0f} %"
        reason = (f"{gpu_txt}: all analyzers capped at {cap:g} fps "
                  f"({', '.join(names[:4])}{'…' if len(names) > 4 else ''} can't keep up)")
        if cap <= policy.floor:
            reason += f" — at the floor ({policy.floor:g} fps), the GPU can't carry this set"
        return Decision(cap, state, reason, names), overloaded_cycles

    overloaded_cycles = 0
    if cap < policy.target_max and (gpu_pct < GPU_IDLE_PCT or not lagging):
        cap = min(policy.target_max, cap + STEP_UP)

    best_input = max((p.input_fps for p in governed), default=0.0)
    if best_input and best_input < policy.target_min:
        return Decision(cap, "input_limited",
                        f"Drones send only {best_input:.0f} fps — raise the edge send rate "
                        f"to reach {policy.target_min:g}–{policy.target_max:g} fps"), 0
    if lagging:     # shortfall without GPU pressure: CPU / DB bound
        names = sorted({f"{p.service}/{p.drone_id}" for p in lagging})
        return Decision(cap, "ok", f"GPU has headroom; {', '.join(names[:4])} limited by CPU/IO",
                        names), 0
    return Decision(cap, "ok", f"All analyzers within target (cap {cap:g} fps)"), 0


def gpu_util_pct() -> Optional[float]:
    try:
        import pynvml  # noqa: PLC0415  (nvidia-ml-py)
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


class Governor:
    def __init__(self, redis_factory, gpu_reader=gpu_util_pct):
        self._redis = redis_factory
        self._gpu = gpu_reader
        self.policy = Policy.from_env()
        self.cap = self.policy.target_max
        self._overloaded = 0
        self.enabled = os.getenv("GOVERNOR_ENABLED", "true").lower() == "true"

    async def _load_policy(self, client) -> None:
        raw = await client.get(POLICY_KEY)
        p = Policy.parse(raw) if raw else None
        if p is None:
            p = Policy.from_env()
        if p != self.policy:
            log.info("FPS policy %s", asdict(p))
            self.policy = p
        self.cap = max(self.policy.floor, min(self.policy.target_max, self.cap))

    async def cycle(self) -> dict:
        client = self._redis()
        await self._load_policy(client)
        raw = await client.mget([STATS_KEY.format(service=s) for s in ALL_SERVICES])
        stats = {}
        for svc, r in zip(ALL_SERVICES, raw):
            if r:
                try:
                    stats[svc] = json.loads(r)
                except ValueError:
                    pass
        gpu_pct = await asyncio.to_thread(self._gpu)
        pairs = pairs_from_stats(stats)

        if self.enabled:
            decision, self._overloaded = decide(self.cap, self.policy, pairs, gpu_pct, self._overloaded)
            self.cap = decision.cap
            pipe = client.pipeline()
            for svc in GOVERNED:
                pipe.set(CAP_KEY.format(service=svc), json.dumps({"cap": self.cap}), ex=CAP_TTL_S)
            await pipe.execute()
        else:
            decision = Decision(self.policy.target_max, "disabled",
                                "Governor disabled (GOVERNOR_ENABLED=false): static DVAS_FPS_MAX caps")

        gpu_info = next((doc.get("gpu") for doc in stats.values() if doc.get("gpu")), None)
        status = {
            "enabled": self.enabled,
            "policy": asdict(self.policy),
            "cap_fps": self.cap,
            "state": decision.state,
            "reason": decision.reason,
            "lagging": decision.lagging,
            "gpu_util_pct": gpu_pct,
            "gpu": gpu_info,
            "services": {
                svc: {
                    "governed": svc in GOVERNED,
                    "cap_fps": doc.get("cap_fps"),
                    "avg_batch": doc.get("avg_batch"),
                    "drones": doc.get("drones") or {},
                }
                for svc, doc in stats.items()
            },
            "updated_at": time.time(),
        }
        await client.set(STATUS_KEY, json.dumps(status), ex=STATUS_TTL_S)
        return status

    async def run(self) -> None:
        log.info("FPS governor running (enabled=%s, policy=%s)", self.enabled, asdict(self.policy))
        while True:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Governor cycle failed: %s", exc)
            await asyncio.sleep(CYCLE_S)
