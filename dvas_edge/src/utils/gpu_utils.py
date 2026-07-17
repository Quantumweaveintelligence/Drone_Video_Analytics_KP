"""
src/utils/gpu_utils.py
======================
GPU capability detection and live VRAM/utilisation query.

Detects:
  • nvidia-ml-py3  → live GPU stats (VRAM, utilisation)
  • FFMPEG CUVID   → h264_cuvid hardware decoder availability
  • cv2.cuda       → OpenCV CUDA (not available in pip build; informational only)

Usage:
    from src.utils.gpu_utils import detect_gpu, query_gpu_stats, GPUInfo
    info = detect_gpu()
    if info.cuvid_available:
        # use h264_cuvid decoder
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

from src.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class GPUInfo:
    """Static capabilities detected at startup."""
    cuda_available: bool = False        # any CUDA path is usable
    cuvid_available: bool = False       # FFMPEG h264_cuvid NVDEC
    opencv_cuda: bool = False           # cv2.cuda module present (custom OpenCV build)
    cupy_available: bool = False        # CuPy GPU arrays (standard pip install)
    device_name: str = "N/A"
    driver_version: str = "N/A"
    vram_total_mb: int = 0
    nvml_available: bool = False        # live stats via pynvml


@dataclass
class GPUStats:
    """Live GPU stats (sampled on demand)."""
    gpu_util_pct: int = 0
    vram_used_mb: int = 0
    vram_total_mb: int = 0
    temp_c: int = 0


# ── Module-level cache ────────────────────────────────────────────────────────
_gpu_info: Optional[GPUInfo] = None
_nvml_handle = None          # pynvml device handle


def detect_gpu() -> GPUInfo:
    """
    Run once at startup.  Returns GPUInfo with all detected capabilities.
    Results are cached; repeated calls return the same object.
    """
    global _gpu_info, _nvml_handle
    if _gpu_info is not None:
        return _gpu_info

    info = GPUInfo()

    # ── 1. cv2.cuda (OpenCV CUDA build — only present in custom builds) ───────
    try:
        import cv2
        n = cv2.cuda.getCudaEnabledDeviceCount()
        if n > 0:
            info.opencv_cuda = True
            info.cuda_available = True
            log.info("gpu_opencv_cuda_available", device_count=n)
    except Exception:
        pass

    # ── 2. CuPy (GPU array library — works with standard CUDA install) ────────
    try:
        import cupy as cp  # type: ignore
        _ = cp.cuda.Device(0).attributes        # probe device 0
        n_cp = cp.cuda.runtime.getDeviceCount()
        if n_cp > 0:
            info.cupy_available = True
            info.cuda_available = True
            props = cp.cuda.runtime.getDeviceProperties(0)
            # props is a dict on modern CuPy
            if isinstance(props, dict):
                info.device_name = props.get("name", info.device_name)
            log.info("gpu_cupy_available", device_count=n_cp, device=info.device_name)
    except Exception as e:
        log.debug("gpu_cupy_unavailable", reason=str(e))

    # ── 3. pynvml (nvidia-ml-py3) — device info + live stats ─────────────────
    try:
        import pynvml
        pynvml.nvmlInit()
        _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem  = pynvml.nvmlDeviceGetMemoryInfo(_nvml_handle)
        info.device_name    = pynvml.nvmlDeviceGetName(_nvml_handle)
        info.driver_version = pynvml.nvmlSystemGetDriverVersion()
        info.vram_total_mb  = mem.total // (1024 * 1024)
        info.cuda_available = True
        info.nvml_available = True
        log.info(
            "gpu_nvml_detected",
            device=info.device_name,
            vram_total_mb=info.vram_total_mb,
            driver=info.driver_version,
        )
    except Exception as e:
        log.debug("gpu_nvml_unavailable", reason=str(e))
        # Fallback: parse nvidia-smi
        try:
            r = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                if len(parts) >= 3:
                    info.device_name    = parts[0]
                    info.vram_total_mb  = int(parts[1])
                    info.driver_version = parts[2]
                    info.cuda_available = True
        except Exception:
            pass

    # ── 4. FFMPEG CUVID (h264_cuvid via NVDEC) ───────────────────────────────
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-codecs"],
            capture_output=True, text=True, timeout=8,
        )
        if "h264_cuvid" in r.stdout:
            info.cuvid_available = True
            log.info("gpu_cuvid_available", decoder="h264_cuvid")
        else:
            log.warning("gpu_cuvid_not_found_in_ffmpeg")
    except Exception as e:
        log.warning("gpu_ffmpeg_check_failed", error=str(e))

    _gpu_info = info

    log.info(
        "gpu_detection_complete",
        device=info.device_name,
        cuda=info.cuda_available,
        cuvid=info.cuvid_available,
        opencv_cuda=info.opencv_cuda,
        cupy=info.cupy_available,
        nvml=info.nvml_available,
        vram_total_mb=info.vram_total_mb,
    )
    return info


def query_gpu_stats() -> Optional[GPUStats]:
    """
    Return live GPU utilisation/VRAM stats.
    Returns None if pynvml is not available.
    """
    if _nvml_handle is None:
        return None
    try:
        import pynvml
        util = pynvml.nvmlDeviceGetUtilizationRates(_nvml_handle)
        mem  = pynvml.nvmlDeviceGetMemoryInfo(_nvml_handle)
        temp = pynvml.nvmlDeviceGetTemperature(_nvml_handle, pynvml.NVML_TEMPERATURE_GPU)
        return GPUStats(
            gpu_util_pct  = util.gpu,
            vram_used_mb  = mem.used  // (1024 * 1024),
            vram_total_mb = mem.total // (1024 * 1024),
            temp_c        = temp,
        )
    except Exception:
        return None
