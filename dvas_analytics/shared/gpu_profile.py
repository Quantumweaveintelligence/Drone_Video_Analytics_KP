# -*- coding: utf-8 -*-
"""
DVAS Analytics — GPU profile (RunPod plan Phase 9.3 / 9.10).

Detects the GPU this process runs on and derives how hard the analyzers may
drive it: FP16 inference, cuDNN autotuning and how many drones' frames go
through the detector in one batch. Inference resolution is never changed here
(that would change detection accuracy).

Tiers (by VRAM):
  small   < 8 GB    e.g. 4 GB laptop / edge GPU   → batch 1
  medium  8–24 GB   e.g. RTX 4090, A10            → batch 2
  large   24–64 GB  e.g. L40S 48 GB               → batch 4
  xlarge  ≥ 64 GB   e.g. RTX PRO 6000 96 GB       → batch 8

Env overrides: DVAS_FP16 (auto|true|false), DVAS_MAX_BATCH (int),
DVAS_CUDNN_BENCHMARK (true|false).
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Optional

log = logging.getLogger(__name__)

# (min VRAM GB, tier, drones per detector batch)
_TIERS = (
    (64.0, "xlarge", 8),
    (24.0, "large", 4),
    (8.0, "medium", 2),
    (0.0, "small", 1),
)

# FP16 tensor cores from Volta (7.0) on; Pascal (6.x) FP16 is slower than FP32
_FP16_MIN_CC = (7, 0)


@dataclass(frozen=True)
class GpuProfile:
    available: bool
    name: str
    vram_gb: float
    compute_capability: str     # "8.9"
    sm_count: int
    tier: str                   # cpu | small | medium | large | xlarge
    fp16: bool
    cudnn_benchmark: bool
    max_batch: int

    def as_dict(self) -> dict:
        return asdict(self)


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None    # unset or "auto"


def build_profile(name: str, vram_gb: float, cc: tuple, sm_count: int) -> GpuProfile:
    """Pure derivation from device properties (unit-tested without a GPU)."""
    tier, batch = next((t, b) for floor, t, b in _TIERS if vram_gb >= floor)
    fp16 = tuple(cc) >= _FP16_MIN_CC
    # Turing GTX 16xx: compute 7.5 but no tensor cores, and FP16 output is unstable
    if "GTX 16" in name.upper():
        fp16 = False
    fp16_env = _env_bool("DVAS_FP16")
    if fp16_env is not None:
        fp16 = fp16_env
    batch_env = os.getenv("DVAS_MAX_BATCH", "").strip()
    if batch_env:
        try:
            batch = max(1, min(32, int(batch_env)))
        except ValueError:
            log.warning("DVAS_MAX_BATCH=%r is not a number; using %d", batch_env, batch)
    bench_env = _env_bool("DVAS_CUDNN_BENCHMARK")
    return GpuProfile(
        available=True,
        name=name,
        vram_gb=round(vram_gb, 1),
        compute_capability=f"{cc[0]}.{cc[1]}",
        sm_count=int(sm_count),
        tier=tier,
        fp16=fp16,
        cudnn_benchmark=True if bench_env is None else bench_env,
        max_batch=batch,
    )


CPU_PROFILE = GpuProfile(
    available=False, name="CPU", vram_gb=0.0, compute_capability="", sm_count=0,
    tier="cpu", fp16=False, cudnn_benchmark=False, max_batch=1,
)

_profile: Optional[GpuProfile] = None


def detect() -> GpuProfile:
    """Profile of cuda:0 (cached). Falls back to CPU_PROFILE without CUDA."""
    global _profile
    if _profile is not None:
        return _profile
    try:
        import torch  # noqa: PLC0415  (heavy; only analyzers need it)
        if not torch.cuda.is_available():
            _profile = CPU_PROFILE
        else:
            p = torch.cuda.get_device_properties(0)
            _profile = build_profile(
                p.name, p.total_memory / 1024 ** 3, (p.major, p.minor), p.multi_processor_count,
            )
    except Exception as exc:
        log.warning("GPU detection failed, running on CPU profile: %s", exc)
        _profile = CPU_PROFILE
    log.info("GPU profile: %s", _profile.as_dict())
    return _profile


def apply_torch_settings(profile: Optional[GpuProfile] = None) -> GpuProfile:
    """Process-wide torch flags for the detected GPU. Call once at service start."""
    profile = profile or detect()
    if not profile.available:
        return profile
    try:
        import torch  # noqa: PLC0415
        # Input sizes are fixed per service, so autotuned kernels are reused every frame
        torch.backends.cudnn.benchmark = profile.cudnn_benchmark
        # TF32 matmuls on Ampere+ (cc ≥ 8.0): FP32 paths (crowd, Re-ID) get faster too
        if tuple(int(x) for x in profile.compute_capability.split(".")) >= (8, 0):
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
    except Exception as exc:
        log.warning("Could not apply torch GPU settings: %s", exc)
    return profile


def predict_kwargs(device=None) -> dict:
    """Common Ultralytics predict() arguments for this GPU."""
    profile = detect()
    kw = {"half": profile.fp16 and profile.available, "verbose": False}
    if device is not None:
        kw["device"] = device
    return kw
