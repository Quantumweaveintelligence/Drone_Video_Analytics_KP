# -*- coding: utf-8 -*-
"""
Crowd Analyzer — headless adaptation of crowd_live_analysis.py.
Removes all OpenCV window/display code; exposes analyze_frame() that returns
a JSON-serializable CrowdAnalysisResult dict.
"""
import base64
import io
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Tuple

import cv2
import numpy as np
import torch
import torch.nn as nn

log = logging.getLogger(__name__)

# ─── Density levels (count threshold, label, BGR colour) ─────────────────────
_DENSITY_LEVELS = [
    (5,      "Empty",      [180, 180, 180]),
    (20,     "Sparse",     [50,  220,  50]),
    (50,     "Moderate",   [0,   200, 255]),
    (100,    "Dense",      [0,   140, 255]),
    (200,    "Very Dense", [0,    60, 220]),
    (999999, "Extreme",    [0,     0, 200]),
]


def get_density_label(count: float) -> Tuple[str, list]:
    for threshold, label, colour in _DENSITY_LEVELS:
        if count < threshold:
            return label, colour
    return "Extreme", [0, 0, 200]


# ─── FourColumnMDNN (identical architecture to crowd_live_analysis.py) ─────────
class FourColumnMDNN(nn.Module):
    """Multi-scale density-map CNN — ShanghaiTech/Mall trained."""

    def __init__(self):
        super().__init__()
        self.column1 = nn.Sequential(
            nn.Conv2d(3, 16, 11, padding=5), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 9,  padding=4), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 7,  padding=3), nn.BatchNorm2d(64), nn.ReLU(),
        )
        self.column2 = nn.Sequential(
            nn.Conv2d(3, 16, 9,  padding=4), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 7,  padding=3), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 5,  padding=2), nn.BatchNorm2d(64), nn.ReLU(),
        )
        self.column3 = nn.Sequential(
            nn.Conv2d(3, 20, 7,  padding=3), nn.BatchNorm2d(20), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(20, 40, 5,  padding=2), nn.BatchNorm2d(40), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(40, 80, 3,  padding=1), nn.BatchNorm2d(80), nn.ReLU(),
        )
        self.column4 = nn.Sequential(
            nn.Conv2d(3, 24, 5,  padding=2), nn.BatchNorm2d(24), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(24, 48, 3,  padding=1), nn.BatchNorm2d(48), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(48, 96, 3,  padding=1), nn.BatchNorm2d(96), nn.ReLU(),
        )
        self.fusion = nn.Sequential(
            nn.Conv2d(64 + 64 + 80 + 96, 64, 1), nn.ReLU(),
            nn.Conv2d(64, 1, 1), nn.ReLU(),
        )

    def forward(self, x):
        return self.fusion(
            torch.cat((self.column1(x), self.column2(x),
                       self.column3(x), self.column4(x)), dim=1)
        )


# ─── Headless analyser ────────────────────────────────────────────────────────
class CrowdAnalyzer:
    """
    Headless crowd counter/density estimator.
    Exposes analyze_frame() returning a plain dict ready for JSON serialisation.
    """

    def __init__(
        self,
        model_path: str,
        patch_size: Tuple[int, int] = (224, 224),
        stride: int = 112,
        scale: float = 1.0,
        downsample: int = 2,
        alert_threshold: int = 50,
    ):
        self.patch_size      = patch_size
        self.stride          = stride
        self.scale           = scale
        self.downsample      = downsample
        self.alert_threshold = alert_threshold

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        log.info("CrowdAnalyzer device: %s", self.device)

        self.model   = self._load_model(model_path)
        self._lock   = threading.Lock()
        self._ready  = self.model is not None

    # ── Model loading ──────────────────────────────────────────────────────
    def _load_model(self, path: str) -> Optional[FourColumnMDNN]:
        try:
            m = FourColumnMDNN().to(self.device)
            state = torch.load(path, map_location=self.device)
            m.load_state_dict(state)
            m.eval()
            log.info("Crowd model loaded from %s", path)
            return m
        except Exception as exc:
            log.error("Failed to load crowd model: %s", exc)
            return None

    # ── Core inference ─────────────────────────────────────────────────────
    def _infer(self, frame_bgr: np.ndarray) -> Tuple[float, np.ndarray]:
        """Sliding-window patch inference — identical logic to crowd_live_analysis.py."""
        if self.model is None:
            return 0.0, np.zeros(frame_bgr.shape[:2], dtype=np.float32)

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        oh, ow    = frame_rgb.shape[:2]
        psh, psw  = self.patch_size

        ds = self.downsample
        while ds > 1 and (oh // ds < psh or ow // ds < psw):
            ds -= 1

        if ds > 1:
            proc = cv2.resize(frame_rgb, (ow // ds, oh // ds))
            ph, pw = proc.shape[:2]
        else:
            proc, ph, pw = frame_rgb, oh, ow

        if ph < psh or pw < psw:
            patch = cv2.resize(proc, (psw, psh))
            t = torch.from_numpy(patch.astype(np.float32) / 255.0) \
                    .permute(2, 0, 1).unsqueeze(0).to(self.device)
            with torch.inference_mode():
                dm = self.model(t)[0, 0].cpu().numpy()
            density = cv2.resize(dm, (ow, oh), interpolation=cv2.INTER_LINEAR) * self.scale
            return float(density.sum()), density

        st      = max(1, self.stride // ds) if ds > 1 else self.stride
        density = np.zeros((ph, pw), dtype=np.float32)
        overlap = np.zeros((ph, pw), dtype=np.float32)

        with torch.inference_mode():
            for y in range(0, ph - psh + 1, st):
                for x in range(0, pw - psw + 1, st):
                    ye, xe = min(y + psh, ph), min(x + psw, pw)
                    ah, aw = ye - y, xe - x
                    patch  = proc[y:ye, x:xe]
                    if ah < psh or aw < psw:
                        patch = np.pad(patch, ((0, psh - ah), (0, psw - aw), (0, 0)), mode="edge")
                    t = torch.from_numpy(patch.astype(np.float32) / 255.0) \
                            .permute(2, 0, 1).unsqueeze(0).to(self.device)
                    dm = self.model(t)[0, 0].cpu().numpy()
                    dm_up = cv2.resize(dm, (aw, ah), interpolation=cv2.INTER_LINEAR)
                    density[y:ye, x:xe] += dm_up[:ah, :aw]
                    overlap[y:ye, x:xe] += 1.0

        overlap[overlap == 0] = 1
        density /= overlap
        if self.downsample > 1:
            density = cv2.resize(density, (ow, oh), interpolation=cv2.INTER_LINEAR)
        density *= self.scale
        return float(density.sum()), density

    # ── Heatmap rendering ──────────────────────────────────────────────────
    @staticmethod
    def _density_to_heatmap_b64(density: np.ndarray, original_shape: tuple) -> str:
        """Convert density map to a base64-encoded PNG heatmap."""
        h, w = original_shape[:2]
        if density is None or density.max() == 0:
            blank = np.zeros((h, w, 3), dtype=np.uint8)
            _, buf = cv2.imencode(".png", blank)
            return base64.b64encode(buf).decode()

        dnorm = (density / density.max() * 255).astype(np.uint8)
        heat  = cv2.applyColorMap(dnorm, cv2.COLORMAP_JET)
        heat  = cv2.resize(heat, (w, h))
        _, buf = cv2.imencode(".png", heat)
        return base64.b64encode(buf).decode()

    # ── Public API ─────────────────────────────────────────────────────────
    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        drone_id: str = "DRONE_KL_001",
        include_heatmap: bool = True,
    ) -> dict:
        """
        Analyze a BGR frame and return a dict matching CrowdAnalysisResult.
        """
        t0 = time.perf_counter()
        with self._lock:
            count, density = self._infer(frame_bgr)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        label, colour = get_density_label(count)
        alert = count >= self.alert_threshold

        heatmap_b64 = None
        if include_heatmap:
            heatmap_b64 = self._density_to_heatmap_b64(density, frame_bgr.shape)

        return {
            "drone_id":        drone_id,
            "ts":              datetime.now(timezone.utc).isoformat(),
            "estimated_count": round(count, 2),
            "density_label":   label,
            "density_colour":  colour,
            "density_per_sqm": None,
            "heatmap_b64":     heatmap_b64,
            "alert_triggered": alert,
            "inference_ms":    round(elapsed_ms, 1),
        }

    @property
    def ready(self) -> bool:
        return self._ready
