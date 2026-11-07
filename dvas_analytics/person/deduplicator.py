# -*- coding: utf-8 -*-
"""
Face De-duplicator — ResNet50 embeddings + Agglomerative Clustering.
Embeddings stored in Postgres face_embeddings table.
Duplicates are marked with is_duplicate=True and cluster_id set.
"""
import asyncio
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple

import cv2
import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms
from sklearn.cluster import AgglomerativeClustering

log = logging.getLogger(__name__)

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
    """Extract a 2048-dim ResNet50 embedding from JPEG bytes."""
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


def extract_embeddings_from_bytes(
    model: nn.Module,
    images: List[bytes],
    device: torch.device,
) -> List[Optional[List[float]]]:
    """2048-dim ResNet50 embeddings of several JPEGs in batches (None where one fails)."""
    import io
    out: List[Optional[List[float]]] = [None] * len(images)
    tensors, index = [], []
    for i, image_bytes in enumerate(images):
        try:
            tensors.append(_PREPROCESS(Image.open(io.BytesIO(image_bytes)).convert("RGB")))
            index.append(i)
        except Exception as exc:
            log.warning("Embedding extraction failed: %s", exc)
    for k in range(0, len(tensors), BATCH_SIZE):
        try:
            with torch.no_grad():
                feat = model(torch.stack(tensors[k:k + BATCH_SIZE]).to(device))
                feat = nn.functional.normalize(feat, p=2, dim=1)
            for j, vec in enumerate(feat.cpu().tolist()):
                out[index[k + j]] = vec
        except Exception as exc:
            log.warning("Embedding extraction failed: %s", exc)
    return out


def cluster_embeddings(
    embeddings: List[List[float]],
    distance_threshold: float = DISTANCE_THRESHOLD,
) -> List[int]:
    """
    Agglomerative Clustering on embeddings (cosine metric, average linkage).
    Returns list of cluster labels.
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
    Given DB row IDs and cluster labels, find which IDs are duplicates.
    Returns (list_of_duplicate_ids, unique_count).
    The sharpest image in each cluster is kept; rest are duplicates.
    """
    clusters: Dict[int, List[Tuple[int, str]]] = {}
    for idx, (row_id, label) in enumerate(zip(ids, labels)):
        clusters.setdefault(label, []).append((idx, row_id))

    duplicate_ids: List[str] = []
    unique_count = 0

    for label, members in clusters.items():
        unique_count += 1
        if len(members) <= 1:
            continue
        if sharpness_scores:
            scored = sorted(members, key=lambda m: sharpness_scores[m[0]], reverse=True)
        else:
            scored = members
        for (_, row_id) in scored[1:]:
            duplicate_ids.append(row_id)

    return duplicate_ids, unique_count


def get_identity_representatives(
    ids: List[str],
    labels: List[int],
    sharpness_scores: Optional[List[float]] = None,
) -> Dict[int, str]:
    """
    Return {cluster_id -> best_embedding_id} mapping.
    The best representative per cluster is the sharpest image.
    """
    clusters: Dict[int, List[Tuple[int, str]]] = {}
    for idx, (row_id, label) in enumerate(zip(ids, labels)):
        clusters.setdefault(label, []).append((idx, row_id))

    representatives: Dict[int, str] = {}
    for label, members in clusters.items():
        if sharpness_scores:
            best = max(members, key=lambda m: sharpness_scores[m[0]])
        else:
            best = members[0]
        representatives[label] = best[1]
    return representatives


async def update_cluster_labels_db(
    ids: List[str],
    labels: List[int],
    duplicate_ids_set: set,
) -> None:
    """
    Write cluster_id and is_duplicate back to face_embeddings rows.
    """
    from shared.db import update_face_embedding_cluster
    tasks = [
        update_face_embedding_cluster(
            embedding_id=row_id,
            cluster_id=label,
            is_duplicate=(row_id in duplicate_ids_set),
        )
        for row_id, label in zip(ids, labels)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def update_person_cluster_labels_db(
    ids: List[str],
    labels: List[int],
    duplicate_ids_set: set,
) -> None:
    """
    Write cluster_id and is_duplicate back to person_embeddings rows.
    """
    from shared.db import update_person_embedding_cluster
    tasks = [
        update_person_embedding_cluster(
            embedding_id=row_id,
            cluster_id=label,
            is_duplicate=(row_id in duplicate_ids_set),
        )
        for row_id, label in zip(ids, labels)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

