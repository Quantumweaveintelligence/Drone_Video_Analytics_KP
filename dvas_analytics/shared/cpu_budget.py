# -*- coding: utf-8 -*-
"""
DVAS Analytics — per-service CPU thread budget (v2 plan R4.4).

The pod's CPU quota is shared by seven analyzer processes. Each one sized its pools on its own:
torch from OMP_NUM_THREADS (quota ÷ 8 for every service alike), OpenCV from the cores it can see
(31 on the L40S pod) — up to 7 × 31 OpenCV threads on 31 cores. dvas-init.sh now gives each
service a share by weight (DVAS_THREADS_<SERVICE>); apply() sets torch and OpenCV to it at service
start. Thread counts do not change what is computed, only how many cores compute it.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def threads_for(service: str) -> Optional[int]:
    """DVAS_THREADS_<SERVICE>, else OMP_NUM_THREADS, else None (leave the libraries' default)."""
    for name in (f"DVAS_THREADS_{service.upper()}", "OMP_NUM_THREADS"):
        try:
            n = int(os.getenv(name, "") or 0)
        except ValueError:
            n = 0
        if n > 0:
            return n
    return None


def apply(service: str) -> Optional[int]:
    """Set this process's torch and OpenCV thread pools to the service's budget. Returns it."""
    n = threads_for(service)
    if n is None:
        return None
    try:
        import torch  # noqa: PLC0415
        torch.set_num_threads(n)
    except Exception as exc:                    # no torch in this venv
        log.debug("torch threads not set: %s", exc)
    try:
        import cv2  # noqa: PLC0415
        cv2.setNumThreads(n)
    except Exception as exc:
        log.debug("OpenCV threads not set: %s", exc)
    log.info("%s: %d CPU threads (torch, OpenCV)", service, n)
    return n
