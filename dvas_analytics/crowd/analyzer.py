import os
import cv2
import time
import base64
import logging
import threading
import numpy as np
from datetime import datetime, timezone
import torch
import torch.nn as nn
from torchvision import transforms

logger = logging.getLogger(__name__)

_DENSITY_LEVELS = [
    (25,     "Low",      [50,  220,  50]),   # Green
    (75,     "Medium",   [0,   220, 255]),   # Yellow/Amber
    (150,    "High",     [0,   140, 255]),   # Orange
    (999999, "Critical", [0,     0,  200]),  # Red
]

def get_density_label(count):
    """Returns (label, color_rgb) based on the estimated count."""
    for threshold, label, color in _DENSITY_LEVELS:
        if count <= threshold:
            return label, color
    # Fallback to highest
    return _DENSITY_LEVELS[-1][1], _DENSITY_LEVELS[-1][2]


class CSRNet(nn.Module):
    def __init__(self, load_weights=False):
        super(CSRNet, self).__init__()
        self.seen = 0
        self.frontend_feat = [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512]
        self.backend_feat = [512, 512, 512, 256, 128, 64]
        self.frontend = make_layers(self.frontend_feat)
        self.backend = make_layers(self.backend_feat, in_channels=512, dilation=True)
        self.output_layer = nn.Conv2d(64, 1, kernel_size=1)
        if not load_weights:
            self._initialize_weights()

    def forward(self, x):
        x = self.frontend(x)
        x = self.backend(x)
        x = self.output_layer(x)
        return x

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.normal_(m.weight, std=0.01)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)


def make_layers(cfg, in_channels=3, batch_norm=False, dilation=False):
    d_rate = 2 if dilation else 1
    layers = []
    for v in cfg:
        if v == 'M':
            layers += [nn.MaxPool2d(kernel_size=2, stride=2)]
        else:
            conv2d = nn.Conv2d(in_channels, v, kernel_size=3, padding=d_rate, dilation=d_rate)
            if batch_norm:
                layers += [conv2d, nn.BatchNorm2d(v), nn.ReLU(inplace=True)]
            else:
                layers += [conv2d, nn.ReLU(inplace=True)]
            in_channels = v
    return nn.Sequential(*layers)


class CrowdAnalyzer:
    FRAME_CACHE_TTL_SEC = 6.0

    def __init__(self, model_path, device=None, alert_threshold=150):
        self._lock = threading.Lock()
        self.alert_threshold = alert_threshold
        
        if device is None:
            self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        else:
            self.device = torch.device(device)
            
        self.ready = False
        self.model = None
        self._model_path = None
        self._model_type = None

        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        
        self._last_annotated_frames = {}
        
        # Session stats
        self._session_counts = []
        self._session_start_time = time.time()
        self._session_alert_timestamps = []
        self._peak_count = 0.0
        self._peak_timestamp = None
        self._peak_frame = None

        if model_path:
            self.switch_model(model_path)

    def _determine_model_type(self, path):
        lower_path = path.lower()
        if 'parta' in lower_path:
            return 'csrnet_parta'
        elif 'partb' in lower_path:
            return 'csrnet_partb'
        return 'csrnet'

    # SHA-256 hashes of trusted model files that use legacy pickle format
    # and cannot load with weights_only=True. Any model NOT on this list
    # must load with weights_only=True (Issue #15).
    _TRUSTED_MODEL_HASHES = frozenset({
        "68383c1053be371ad54b0061ab99fed08c2bfff39de73937f2a4388041ab0128",  # PartAmodel_best.pth (dense crowds / drone)
        "eb4329b111da5d752db0502f2d1bb450728e63490753607bb31e361fc25c9b97",  # partBmodel_best.pth (static surveillance / CCTV)
    })

    def _load_model(self, path):
        try:
            logger.info(f"Loading CSRNet model from {path} to {self.device}")

            # Security: compute model file hash for integrity auditing
            import hashlib
            with open(path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            logger.info(f"Model file SHA-256: {file_hash}")

            model = CSRNet(load_weights=True).to(self.device)

            # Security (Issue #15): Only trusted, hash-verified models may use
            # weights_only=False. Unknown models must use weights_only=True to
            # prevent arbitrary code execution via malicious pickle payloads.
            if file_hash in self._TRUSTED_MODEL_HASHES:
                logger.info("Model hash matches trusted allowlist — loading with legacy pickle")
                checkpoint = torch.load(path, map_location=self.device, weights_only=False)
            else:
                logger.info("Model hash NOT in trusted allowlist — loading with weights_only=True")
                checkpoint = torch.load(path, map_location=self.device, weights_only=True)

            if isinstance(checkpoint, dict) and 'state_dict' in checkpoint:
                model.load_state_dict(checkpoint['state_dict'])
            else:
                model.load_state_dict(checkpoint)
                
            model.eval()
            return model
        except Exception as e:
            logger.error(f"Failed to load model {path}: {e}")
            raise e

    def switch_model(self, new_model_path):
        """Thread-safe method to switch the active model weights with rollback on failure."""
        with self._lock:
            old_ready = self.ready
            old_model = self.model
            old_path = self._model_path
            old_type = self._model_type
            try:
                self.ready = False
                loaded = self._load_model(new_model_path)
                self.model = loaded
                self._model_path = new_model_path
                self._model_type = self._determine_model_type(new_model_path)
                self.ready = True
                logger.info(f"Switched model to {self._model_type} ({new_model_path})")
            except Exception as e:
                self.model = old_model
                self._model_path = old_path
                self._model_type = old_type
                self.ready = old_ready
                logger.error(f"Failed to switch to {new_model_path}: {e}. Restored {old_type} (ready={old_ready})")
                raise e

    def reset_session(self):
        """Clears session data."""
        with self._lock:
            self._session_counts = []
            self._session_start_time = time.time()
            self._session_alert_timestamps = []
            self._peak_count = 0.0
            self._peak_timestamp = None
            self._peak_frame = None

    def get_session_summary(self):
        """Returns statistical summary of the current session."""
        with self._lock:
            total_frames = len(self._session_counts)
            duration = time.time() - self._session_start_time
            
            if total_frames == 0:
                return {
                    "total_frames": 0,
                    "duration_seconds": duration,
                    "mean_count": 0,
                    "min_count": 0,
                    "max_count": 0,
                    "std_dev": 0,
                    "peak_count": 0,
                    "peak_timestamp": None,
                    "peak_frame": None,
                    "density_distribution": {},
                    "alert_timestamps": self._session_alert_timestamps.copy()
                }

            counts = np.array(self._session_counts)
            
            dist = {}
            for c in counts:
                lbl, _ = get_density_label(c)
                dist[lbl] = dist.get(lbl, 0) + 1
            for k in dist:
                dist[k] = (dist[k] / total_frames) * 100

            return {
                "total_frames": total_frames,
                "duration_seconds": duration,
                "mean_count": float(np.mean(counts)),
                "min_count": float(np.min(counts)),
                "max_count": float(np.max(counts)),
                "std_dev": float(np.std(counts)),
                "peak_count": self._peak_count,
                "peak_timestamp": self._peak_timestamp,
                "peak_frame": self._peak_frame,
                "density_distribution": dist,
                "alert_timestamps": self._session_alert_timestamps.copy()
            }

    def _infer(self, frame_bgr, max_dim=1536):
        h, w = frame_bgr.shape[:2]
        
        # Resize if longer side > max_dim
        if h > max_dim or w > max_dim:
            scale = max_dim / max(h, w)
            new_h, new_w = int(h * scale), int(w * scale)
            frame_resized = cv2.resize(frame_bgr, (new_w, new_h))
        else:
            frame_resized = frame_bgr.copy()
            
        # Pad to multiples of 8
        h_r, w_r = frame_resized.shape[:2]
        pad_h = (8 - h_r % 8) % 8
        pad_w = (8 - w_r % 8) % 8
        
        if pad_h > 0 or pad_w > 0:
            frame_padded = cv2.copyMakeBorder(frame_resized, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT)
        else:
            frame_padded = frame_resized
            
        frame_rgb = cv2.cvtColor(frame_padded, cv2.COLOR_BGR2RGB)
        tensor_img = self.transform(frame_rgb).unsqueeze(0).to(self.device)
        
        with torch.no_grad():
            output = self.model(tensor_img)
            
        density_map = output.squeeze().cpu().numpy()
        return density_map

    def generate_annotated_frame(self, drone_id, frame_bgr, density_map, count):
        """Creates a pure heatmap overlaid JPEG frame without text embeddings."""
        h, w = frame_bgr.shape[:2]
        density_resized = cv2.resize(density_map, (w, h), interpolation=cv2.INTER_CUBIC)
        
        d_min, d_max = density_resized.min(), density_resized.max()
        if d_max > d_min:
            density_norm = ((density_resized - d_min) / (d_max - d_min) * 255).astype(np.uint8)
        else:
            density_norm = np.zeros_like(density_resized, dtype=np.uint8)
            
        heatmap = cv2.applyColorMap(density_norm, cv2.COLORMAP_JET)
        blended = cv2.addWeighted(frame_bgr, 0.55, heatmap, 0.45, 0)
        
        _, jpeg = cv2.imencode('.jpg', blended, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        jpeg_bytes = jpeg.tobytes()
        
        with self._lock:
            self._last_annotated_frames[drone_id] = {
                "jpeg": jpeg_bytes,
                "ts": time.time(),
                "count": count,
                "label": get_density_label(count)[0] if count is not None else "Unknown",
            }
            
        return jpeg_bytes

    def get_last_annotated_frame(self, drone_id=""):
        """Returns (jpeg_bytes, matched_drone_id, age_sec, count, label) or (None, None, 0, 0, '')."""
        with self._lock:
            if not self._last_annotated_frames:
                return None, None, 0.0, 0, ""

            entry = None
            matched_id = None
            if drone_id:
                # Try exact match
                if drone_id in self._last_annotated_frames:
                    entry = self._last_annotated_frames[drone_id]
                    matched_id = drone_id
                else:
                    # Try aliases
                    try:
                        from shared.config import get_drone_aliases
                        aliases = get_drone_aliases(drone_id)
                        for alias in aliases:
                            if alias in self._last_annotated_frames:
                                entry = self._last_annotated_frames[alias]
                                matched_id = alias
                                break
                    except Exception:
                        pass

            # Fallback to most recent frame across all active drones if specific one not found
            if not entry:
                matched_id = max(self._last_annotated_frames, key=lambda d: self._last_annotated_frames[d]["ts"])
                entry = self._last_annotated_frames[matched_id]

            age = time.time() - entry["ts"]
            if age > self.FRAME_CACHE_TTL_SEC:
                return None, None, age, 0, ""
                
            return entry["jpeg"], matched_id, age, entry.get("count", 0), entry.get("label", "")

    @staticmethod
    def _density_to_heatmap_b64(density_map):
        d_min, d_max = density_map.min(), density_map.max()
        if d_max > d_min:
            density_norm = ((density_map - d_min) / (d_max - d_min) * 255).astype(np.uint8)
        else:
            density_norm = np.zeros_like(density_map, dtype=np.uint8)
            
        heatmap = cv2.applyColorMap(density_norm, cv2.COLORMAP_JET)
        _, buffer = cv2.imencode('.jpg', heatmap)
        return base64.b64encode(buffer).decode('utf-8')

    def analyze_frame(self, frame_bgr, drone_id="DRONE_KL_001", include_heatmap=True):
        if not self.ready or self.model is None:
            return None
            
        timestamp = time.time()
            
        start_t = time.perf_counter()
        
        with self._lock:
            density_map = self._infer(frame_bgr)
            
        count = max(0.0, float(np.sum(density_map)))
        
        # Determine labels and alerts
        label, color = get_density_label(count)
        alert_triggered = count >= self.alert_threshold
        
        # Calculate per sqm pseudo-estimate
        h, w = frame_bgr.shape[:2]
        density_per_sqm = count / (h * w / 10000.0)
        
        # Heatmap base64
        heatmap_b64 = None
        if include_heatmap:
            heatmap_b64 = self._density_to_heatmap_b64(density_map)
            
        # Create and cache annotated frame
        jpeg_bytes = self.generate_annotated_frame(drone_id, frame_bgr, density_map, count)
        
        # Update session stats
        ts_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._session_counts.append(count)
            if alert_triggered:
                self._session_alert_timestamps.append(ts_iso)
            
            if count > self._peak_count:
                self._peak_count = count
                self._peak_timestamp = ts_iso
                # Store base64 of the annotated frame as the peak_frame
                self._peak_frame = base64.b64encode(jpeg_bytes).decode('utf-8')
        
        end_t = time.perf_counter()
        inference_ms = (end_t - start_t) * 1000.0
        
        return {
            "drone_id": drone_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "estimated_count": count,
            "density_label": label,
            "density_colour": color,
            "density_per_sqm": density_per_sqm,
            "heatmap_b64": heatmap_b64,
            "alert_triggered": alert_triggered,
            "inference_ms": inference_ms,
            "model_type": self._model_type
        }
