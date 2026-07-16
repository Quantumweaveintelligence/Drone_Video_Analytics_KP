# -*- coding: utf-8 -*-
"""
Face De-duplicator — adapted from person_dedupe.py.
Uses ResNet50 embeddings + Agglomerative Clustering.
Embeddings are stored in Postgres face_embeddings table.
Duplicates are deleted from the DB after clustering.
"""
import logging
import numpy as np
from typing import List, Optional, Tuple

import cv2
import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms
from sklearn.cluster import AgglomerativeClustering

log = logging.getLogger(__name__)

# Best approach: cosine similarity with 0.25 threshold (proven in person_dedupe.py)
DISTANCE_THRESHOLD = 0.25
BATCH_SIZE = 32

_PREPROCESS = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def load_resnet_model(device: torch.device) -> nn.Module:
    """Load ResNet50 feature extractor (no FC layer)."""
    try:
        weights = models.ResNet50_Weights.DEFAULT
        model   = models.resnet50(weights=weights)
    except Exception:
        model   = models.resnet50(pretrained=True)
    model.fc = nn.Identity()
    model.to(device)
    model.eval()
    log.info("ResNet50 feature extractor loaded on %s", device)
    return model


def get_sharpness_score(img_bgr: np.ndarray) -> float:
    """Laplacian variance sharpness metric — higher = sharper."""
    try:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 0.0


def extract_embedding_from_bytes(
    model: nn.Module,
    image_bytes: bytes,
    device: torch.device,
) -> Optional[List[float]]:
    """
    Extract a 2048-dim ResNet50 embedding from JPEG bytes.
    Returns normalized float list or None on failure.
    """
    try:
        import io
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        tensor  = _PREPROCESS(pil_img).unsqueeze(0).to(device)
        with torch.no_grad():
            feat = model(tensor)
            feat = nn.functional.normalize(feat, p=2, dim=1)
        return feat[0].cpu().tolist()
    except Exception as exc:
        log.warning("Embedding extraction failed: %s", exc)
        return None


def extract_embedding_batch(
    model: nn.Module,
    image_bytes_list: List[bytes],
    device: torch.device,
) -> List[Optional[List[float]]]:
    """Batch embedding extraction."""
    results = []
    for i in range(0, len(image_bytes_list), BATCH_SIZE):
        batch_bytes = image_bytes_list[i:i + BATCH_SIZE]
        tensors = []
        valid_idx = []
        for j, b in enumerate(batch_bytes):
            emb = extract_embedding_from_bytes(model, b, device)
            if emb:
                tensors.append(emb)
                valid_idx.append(i + j)
            results.append(None)
        for idx, emb in zip(valid_idx, tensors):
            results[idx] = emb
    return results


def cluster_embeddings(
    embeddings: List[List[float]],
    distance_threshold: float = DISTANCE_THRESHOLD,
) -> List[int]:
    """
    Run Agglomerative Clustering on embeddings.
    Returns list of cluster labels (integers).
    Best approach: cosine metric + average linkage (from person_dedupe.py)
    """
    if len(embeddings) < 2:
        return list(range(len(embeddings)))

    arr = np.array(embeddings, dtype=np.float32)
    try:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=distance_threshold,
            metric="cosine",
            linkage="average",
        ).fit(arr)
    except TypeError:
        # Fallback for older sklearn
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=distance_threshold,
            affinity="euclidean",
            linkage="average",
        ).fit(arr)

    return clustering.labels_.tolist()


def find_duplicates_in_clusters(
    ids: List[str],
    labels: List[int],
    sharpness_scores: Optional[List[float]] = None,
) -> Tuple[List[str], int]:
    """
    Given a list of DB row IDs and cluster labels, determine which IDs
    are duplicates (i.e. not the best representative of their cluster).
    Returns (list_of_duplicate_ids_to_delete, unique_count).
    """
    clusters = {}
    for idx, (row_id, label) in enumerate(zip(ids, labels)):
        clusters.setdefault(label, []).append((idx, row_id))

    duplicate_ids = []
    unique_count  = 0

    for label, members in clusters.items():
        unique_count += 1
        if len(members) <= 1:
            continue
        # Pick the sharpest as the winner; if no sharpness data, pick first
        if sharpness_scores:
            scored = sorted(members, key=lambda m: sharpness_scores[m[0]], reverse=True)
        else:
            scored = members
        # All except the winner are duplicates
        for (_, row_id) in scored[1:]:
            duplicate_ids.append(row_id)

    return duplicate_ids, unique_count
