# -*- coding: utf-8 -*-
"""
ArcFace Face Recognition — ONNX Runtime inference, watchlist matching,
and alert deduplication for the DVAS person detection pipeline.

Key design:
  - ArcFace R100 ONNX model produces 512-dim L2-normalised embeddings.
  - WatchlistMatcher uses double-buffer pattern for lock-free gallery reload.
  - AlertDeduplicator prevents the same person from triggering > 1 alert
    within a configurable cooldown window.

CUDA note:
  ONNX Runtime CUDAExecutionProvider co-exists with PyTorch CUDA 12.1.
  If CUDA OOM occurs, the recognizer silently falls back to CPU.
"""
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ─── Quality gates (from shared.config, with fallback defaults) ───────────────
try:
    from shared.config import cfg as _cfg
    MIN_FACE_SIZE = _cfg.ARCFACE_QUALITY_MIN_SIZE           # default 30
    MIN_LAPLACIAN_VARIANCE = _cfg.ARCFACE_QUALITY_MIN_BLUR  # default 50.0
except Exception:
    MIN_FACE_SIZE = 30
    MIN_LAPLACIAN_VARIANCE = 50
ARCFACE_INPUT_SIZE = 112    # ArcFace R100 expected input size
UPSCALE_THRESHOLD = 56      # faces smaller than this get Lanczos 2× upscale


def compute_sharpness(face_bgr: np.ndarray) -> float:
    """Laplacian-variance sharpness metric (higher = sharper)."""
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def enhance_face_crop(crop_bgr: np.ndarray) -> np.ndarray:
    """
    Tier-1 face enhancement for drone footage.

    Pipeline:
      1. Convert to LAB → CLAHE on L channel → merge back to BGR.
      2. If crop < 56×56: Lanczos 2× upscale.
      3. Gentle denoise with fastNlMeansDenoisingColored.

    Latency: ~1.5 ms per crop (CPU).
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return crop_bgr
    h, w = crop_bgr.shape[:2]
    if h < 4 or w < 4:
        return crop_bgr

    # CLAHE on L channel of LAB colour space
    lab = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)
    enhanced = cv2.merge([l_ch, a_ch, b_ch])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    # Lanczos upscale for very small crops
    if min(h, w) < UPSCALE_THRESHOLD:
        enhanced = cv2.resize(
            enhanced,
            (max(w * 2, ARCFACE_INPUT_SIZE), max(h * 2, ARCFACE_INPUT_SIZE)),
            interpolation=cv2.INTER_LANCZOS4,
        )

    # Gentle denoise
    enhanced = cv2.fastNlMeansDenoisingColored(enhanced, None, 5, 5, 7, 21)
    return enhanced


# ═════════════════════════════════════════════════════════════════════════════
#  ArcFace ONNX Recognizer
# ═════════════════════════════════════════════════════════════════════════════

class ArcFaceRecognizer:
    """
    ArcFace R100 face recognition via ONNX Runtime.

    - Input:  112×112 BGR face crop.
    - Output: 512-dim L2-normalised embedding.
    - Falls back to CPUExecutionProvider if CUDA is unavailable or OOMs.
    """

    def __init__(self, model_path: str):
        self._model_path = model_path
        self._session = None
        self._input_name: Optional[str] = None
        self._provider: str = "none"
        self.ready = False

        self._load_model()

    # ── Model loading ─────────────────────────────────────────────────────

    def _load_model(self):
        """Load the ONNX model with GPU-first, CPU-fallback strategy."""
        try:
            import onnxruntime as ort
        except ImportError:
            log.error("onnxruntime not installed — ArcFace recognition disabled")
            return

        import os
        if not os.path.isfile(self._model_path):
            log.error("ArcFace model not found at %s — recognition disabled", self._model_path)
            return

        providers_to_try = [
            (["CUDAExecutionProvider", "CPUExecutionProvider"], "CUDA+CPU"),
            (["CPUExecutionProvider"], "CPU-only"),
        ]

        for providers, label in providers_to_try:
            try:
                sess_opts = ort.SessionOptions()
                sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sess_opts.intra_op_num_threads = 2

                self._session = ort.InferenceSession(
                    self._model_path, sess_options=sess_opts, providers=providers
                )
                self._input_name = self._session.get_inputs()[0].name
                active = self._session.get_providers()
                self._provider = active[0] if active else label
                self.ready = True
                log.info(
                    "ArcFace model loaded (%s) — provider: %s",
                    self._model_path, self._provider,
                )
                return
            except Exception as exc:
                log.warning("ArcFace load with %s failed: %s", label, exc)
                self._session = None

        log.error("ArcFace model failed to load with any provider")

    # ── Preprocessing ─────────────────────────────────────────────────────

    def _preprocess(self, face_bgr: np.ndarray) -> np.ndarray:
        """
        Preprocess a BGR face crop for ArcFace inference.

        Steps:
          1. Resize to 112×112.
          2. Convert BGR → RGB.
          3. Transpose HWC → CHW.
          4. Normalise to [-1, +1].
          5. Add batch dim → (1, 3, 112, 112).
        """
        img = cv2.resize(face_bgr, (ARCFACE_INPUT_SIZE, ARCFACE_INPUT_SIZE))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32)
        img = (img / 127.5) - 1.0  # normalise to [-1, 1]
        img = np.transpose(img, (2, 0, 1))  # HWC → CHW
        img = np.expand_dims(img, axis=0)    # add batch dim
        return img

    def preprocess_face(self, face_bgr: np.ndarray) -> np.ndarray:
        """Public alias for _preprocess."""
        return self._preprocess(face_bgr)

    def enhance_face_crop(self, crop_bgr: np.ndarray) -> np.ndarray:
        """Instance method alias for module-level enhance_face_crop."""
        return enhance_face_crop(crop_bgr)

    # ── Embedding extraction ──────────────────────────────────────────────

    def extract_embedding(
        self,
        face_bgr: np.ndarray,
        quality_check: bool = True,
    ) -> Optional[List[float]]:
        """
        Extract a 512-dim ArcFace embedding from a BGR face crop.

        Quality gates:
          - Rejects crops smaller than MIN_FACE_SIZE on either dimension.
          - Rejects crops with Laplacian variance < MIN_LAPLACIAN_VARIANCE.

        Returns:
          512-dim L2-normalised float list, or None if quality gate rejects.
        """
        if not self.ready or self._session is None:
            return None

        if face_bgr is None or face_bgr.size == 0:
            return None

        h, w = face_bgr.shape[:2]

        # Size gate
        if quality_check and (h < MIN_FACE_SIZE or w < MIN_FACE_SIZE):
            log.debug("Face too small (%d×%d) — skipped", w, h)
            return None

        # Blur gate
        if quality_check:
            sharpness = compute_sharpness(face_bgr)
            if sharpness < MIN_LAPLACIAN_VARIANCE:
                log.debug("Face too blurry (lap=%.1f) — skipped", sharpness)
                return None

        try:
            inp = self._preprocess(face_bgr)
            outputs = self._session.run(None, {self._input_name: inp})
            embedding = outputs[0].flatten().astype(np.float64)

            # L2 normalise
            norm = np.linalg.norm(embedding)
            if norm < 1e-10:
                return None
            embedding = (embedding / norm).tolist()
            return embedding

        except Exception as exc:
            log.warning("ArcFace inference error: %s", exc)
            return None

    def extract_embedding_from_bytes(
        self, image_bytes: bytes, quality_check: bool = True
    ) -> Optional[List[float]]:
        """
        Extract embedding from JPEG/PNG bytes (convenience wrapper).
        """
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        return self.extract_embedding(img, quality_check=quality_check)

    def generate_augmented_embeddings(
        self, face_bgr: np.ndarray
    ) -> List[Tuple[List[float], str, float]]:
        """
        Generate the 3 standard augmentation embeddings for watchlist enrollment.

        Returns list of (embedding, augmentation_type, quality_score) tuples.
        Variants: original, horizontal flip, brightness-adjusted.
        """
        results = []

        # 1. Original
        sharpness = compute_sharpness(face_bgr)
        emb = self.extract_embedding(face_bgr, quality_check=False)
        if emb:
            results.append((emb, "original", sharpness))

        # 2. Horizontal flip
        flipped = cv2.flip(face_bgr, 1)
        emb_flip = self.extract_embedding(flipped, quality_check=False)
        if emb_flip:
            results.append((emb_flip, "flip", sharpness))

        # 3. Brightness-adjusted (+30)
        bright = cv2.convertScaleAbs(face_bgr, alpha=1.0, beta=30)
        emb_bright = self.extract_embedding(bright, quality_check=False)
        if emb_bright:
            results.append((emb_bright, "bright", sharpness * 0.95))

        return results


# ═════════════════════════════════════════════════════════════════════════════
#  Watchlist Gallery Matcher (Double-Buffer)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class MatchResult:
    """Result of matching an embedding against the watchlist gallery."""
    watchlist_id: str
    name: str
    category: str
    case_ref: Optional[str]
    similarity: float
    rank: int = 1


@dataclass
class _GallerySnapshot:
    """Immutable snapshot of the watchlist gallery for lock-free reads."""
    matrix: Optional[np.ndarray] = None  # shape (N, 512)
    ids:   List[str]  = field(default_factory=list)
    names: List[str]  = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    case_refs: List[Optional[str]] = field(default_factory=list)
    size:  int = 0


class WatchlistMatcher:
    """
    In-memory watchlist gallery with atomic double-buffer reload.

    On reload, a new _GallerySnapshot is built completely from DB,
    then the reference is swapped under lock. The stream loop always
    reads a consistent snapshot — no partial loads.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = _GallerySnapshot()
        self._last_reload: float = 0.0

    @property
    def gallery_size(self) -> int:
        return self._snapshot.size

    @property
    def last_reload_ts(self) -> float:
        return self._last_reload

    async def reload_gallery(self):
        """
        Atomically reload the in-memory gallery from DB.

        Steps:
          1. Query all active watchlist entries with embeddings.
          2. For multi-embedding persons, compute centroid (average).
          3. Build numpy (N, 512) matrix + parallel metadata lists.
          4. Swap snapshot reference under lock (atomic pointer swap).
        """
        from shared.db import get_all_active_watchlist_with_embeddings

        try:
            entries = await get_all_active_watchlist_with_embeddings()
        except Exception as exc:
            log.error("Gallery reload DB error: %s", exc)
            return

        ids, names, categories, case_refs, centroids = [], [], [], [], []

        for entry in entries:
            embs = entry.get("embeddings", [])
            if not embs:
                continue

            # Compute centroid of all embeddings for this person
            vectors = [e["embedding"] for e in embs if e.get("embedding")]
            if not vectors:
                continue

            centroid = np.mean(vectors, axis=0).astype(np.float64)
            norm = np.linalg.norm(centroid)
            if norm > 1e-10:
                centroid = centroid / norm  # re-normalise after averaging

            ids.append(entry["watchlist_id"])
            names.append(entry["name"])
            categories.append(entry["category"])
            case_refs.append(entry.get("case_ref"))
            centroids.append(centroid)

        # Build new snapshot
        new_snapshot = _GallerySnapshot(
            matrix=np.array(centroids, dtype=np.float64) if centroids else None,
            ids=ids,
            names=names,
            categories=categories,
            case_refs=case_refs,
            size=len(ids),
        )

        # Atomic swap
        with self._lock:
            self._snapshot = new_snapshot
            self._last_reload = time.monotonic()

        log.info("Watchlist gallery reloaded: %d entries", new_snapshot.size)

    def match(
        self,
        embedding: List[float],
        threshold: float = 0.55,
    ) -> Optional[MatchResult]:
        """
        Match a single 512-dim embedding against the gallery.

        Uses vectorised cosine similarity (numpy matmul).
        Returns the best match above threshold, or None.

        Latency: < 0.1 ms for 1000-entry gallery.
        """
        snap = self._snapshot  # read the current snapshot (atomic)
        if snap.size == 0 or snap.matrix is None:
            return None

        query = np.array(embedding, dtype=np.float64)
        norm = np.linalg.norm(query)
        if norm < 1e-10:
            return None
        query = query / norm

        # Cosine similarity = dot product of L2-normalised vectors
        similarities = snap.matrix @ query  # shape (N,)

        best_idx = int(np.argmax(similarities))
        best_sim = float(similarities[best_idx])

        if best_sim < threshold:
            return None

        return MatchResult(
            watchlist_id=snap.ids[best_idx],
            name=snap.names[best_idx],
            category=snap.categories[best_idx],
            case_ref=snap.case_refs[best_idx],
            similarity=round(best_sim, 4),
            rank=1,
        )

    def match_batch(
        self,
        embeddings: List[List[float]],
        threshold: float = 0.55,
    ) -> List[Optional[MatchResult]]:
        """
        Vectorised batch matching — all faces in one frame at once.

        Returns a list parallel to `embeddings` with MatchResult or None.
        """
        snap = self._snapshot
        if snap.size == 0 or snap.matrix is None:
            return [None] * len(embeddings)

        if not embeddings:
            return []

        query_mat = np.array(embeddings, dtype=np.float64)
        norms = np.linalg.norm(query_mat, axis=1, keepdims=True)
        norms = np.where(norms < 1e-10, 1.0, norms)
        query_mat = query_mat / norms

        # (Q, 512) @ (512, N) → (Q, N) similarities
        sim_matrix = query_mat @ snap.matrix.T

        results = []
        for i in range(len(embeddings)):
            best_idx = int(np.argmax(sim_matrix[i]))
            best_sim = float(sim_matrix[i, best_idx])
            if best_sim < threshold:
                results.append(None)
            else:
                results.append(MatchResult(
                    watchlist_id=snap.ids[best_idx],
                    name=snap.names[best_idx],
                    category=snap.categories[best_idx],
                    case_ref=snap.case_refs[best_idx],
                    similarity=round(best_sim, 4),
                    rank=1,
                ))
        return results


# ═════════════════════════════════════════════════════════════════════════════
#  Alert Deduplicator (Temporal)
# ═════════════════════════════════════════════════════════════════════════════

class AlertDeduplicator:
    """
    Prevents duplicate alerts for the same person within a time window.

    Tracks the last alert timestamp for each watchlist_id.
    Only allows a new alert if `cooldown_seconds` have elapsed.
    """

    def __init__(self, cooldown_seconds: int = 30):
        self._recent: Dict[str, float] = {}  # watchlist_id → monotonic timestamp
        self._cooldown = cooldown_seconds
        self._lock = threading.Lock()

    @property
    def cooldown(self) -> int:
        return self._cooldown

    def should_alert(self, watchlist_id: str) -> bool:
        """
        Returns True if enough time has passed since last alert for this person.
        If True, also records the current time as the last alert.
        """
        now = time.monotonic()
        with self._lock:
            last = self._recent.get(watchlist_id, 0.0)
            if now - last < self._cooldown:
                return False
            self._recent[watchlist_id] = now
            return True

    def cleanup(self):
        """Remove stale entries older than 5× cooldown."""
        now = time.monotonic()
        cutoff = now - (self._cooldown * 5)
        with self._lock:
            stale = [k for k, v in self._recent.items() if v < cutoff]
            for k in stale:
                del self._recent[k]

    def reset(self, watchlist_id: Optional[str] = None):
        """Reset cooldown for a specific person or all."""
        with self._lock:
            if watchlist_id:
                self._recent.pop(watchlist_id, None)
            else:
                self._recent.clear()


# ═════════════════════════════════════════════════════════════════════════════
#  Section 65B Evidence Packet Generator
# ═════════════════════════════════════════════════════════════════════════════

def compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest for Section 65B evidence chain."""
    return hashlib.sha256(data).hexdigest()


def build_evidence_metadata(
    alert_id: str,
    watchlist_id: str,
    name: str,
    category: str,
    drone_id: str,
    mission_id: Optional[str],
    tracker_id: Optional[int],
    confidence: float,
    face_crop_sha256: str,
    frame_sha256: str,
    detected_at_iso: str,
) -> dict:
    """
    Build a Section 65B–compliant evidence metadata packet.

    This JSON structure accompanies every watchlist alert and provides
    the chain-of-custody fields required under Section 65B of the
    Indian Evidence Act for digital evidence admissibility.
    """
    return {
        "section_65b": {
            "act": "Indian Evidence Act, 1872 — Section 65B",
            "description": "Certificate authenticating electronic record",
            "system": "DVAS (Drone Video Analytics System) — Kerala Police",
        },
        "alert_id": alert_id,
        "watchlist_id": watchlist_id,
        "subject_name": name,
        "subject_category": category,
        "detection": {
            "drone_id": drone_id,
            "mission_id": mission_id,
            "tracker_id": tracker_id,
            "confidence": confidence,
            "detected_at": detected_at_iso,
        },
        "integrity": {
            "face_crop_sha256": face_crop_sha256,
            "frame_sha256": frame_sha256,
            "hash_algorithm": "SHA-256",
        },
        "generated_by": "DVAS ArcFace Recognition Pipeline v1.0",
    }
