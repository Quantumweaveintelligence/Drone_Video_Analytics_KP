# -*- coding: utf-8 -*-
"""
Re-ID clustering of person (whole-body) and face embeddings — per mission, on a timer.

Found on the pod (Phase 0): clustering every 50 frames over ALL embeddings (32,600 person /
25,500 face rows) ran back to back — minutes per run, AgglomerativeClustering is O(n²),
every row was decrypted on the event loop and every label written with its own UPDATE —
so the person service took up to 1.2 s per frame with the GPU idle.

Now:
  * Every REID_CLUSTER_EVERY_S (60 s) and only for missions with embeddings not yet
    clustered (cluster_id IS NULL). Identities are per mission (person_identities is keyed
    on mission + cluster), so a mission is clustered on its own.
  * A mission with ≤ REID_CLUSTER_MAX embeddings is re-clustered in full with the same
    algorithm as before (average-linkage, cosine, REID_DISTANCE_THRESHOLD). A larger one
    assigns its new embeddings to the nearest cluster centroid (same threshold) or opens
    a new cluster; its centroids are cached in memory.
  * Cluster ids stay stable between runs (a new cluster takes over the id most of its
    members already had) and unique per table (new ids continue after the table's
    maximum), so galleries and identities do not multiply or collide across missions.
  * Decryption and clustering run in worker threads; labels are written with one
    executemany per mission, only for rows that changed.

Duplicates: in each cluster the sharpest person crop (faces: the newest) is the one shown;
the others are marked is_duplicate, as before.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Dict, List, Optional, Tuple

import numpy as np

from shared.config import cfg

log = logging.getLogger(__name__)

EMB_DIM = 2048                 # ResNet50 features (person and face galleries)
FETCH_CHUNK = 2000             # rows per query when a large mission is loaded

TABLES = {"person": "person_embeddings", "face": "face_embeddings"}

# (kind, mission_id) → {cluster_id: [sum vector, count, rep_id, rep_sharpness, rep_object_key, drone_id]}
_centroids: Dict[Tuple[str, Optional[str]], Dict[int, list]] = {}


def _decode(blobs: List[bytes]) -> Tuple[np.ndarray, List[bool]]:
    """Decrypt and L2-normalise embeddings (worker thread). ok[i] False = unreadable row."""
    from shared.crypto import decrypt_vector
    vecs, ok = [], []
    for b in blobs:
        try:
            v = np.asarray(decrypt_vector(bytes(b), expected_dim=EMB_DIM), np.float32) if b else None
        except Exception:
            v = None
        if v is None or v.size == 0 or (vecs and v.size != vecs[0].size):
            ok.append(False)
            continue
        n = float(np.linalg.norm(v))
        vecs.append(v / n if n > 1e-12 else v)
        ok.append(True)
    return (np.stack(vecs) if vecs else np.zeros((0, EMB_DIM), np.float32)), ok


def stable_ids(new_labels: List[int], old_ids: List[Optional[int]], next_id: int) -> Tuple[List[int], int]:
    """Map fresh cluster labels to ids: a cluster keeps the id most of its members already
    had (largest overlap first, each old id used once); others get next_id, next_id+1, …"""
    members: Dict[int, List[int]] = {}
    for i, lab in enumerate(new_labels):
        members.setdefault(lab, []).append(i)
    votes = []
    for lab, idx in members.items():
        c = Counter(old_ids[i] for i in idx if old_ids[i] is not None)
        for old, n in c.items():
            votes.append((n, lab, old))
    votes.sort(key=lambda v: (-v[0], v[1], v[2]))
    mapping: Dict[int, int] = {}
    used = set()
    for n, lab, old in votes:
        if lab not in mapping and old not in used:
            mapping[lab] = old
            used.add(old)
    for lab in sorted(members):
        if lab not in mapping:
            mapping[lab] = next_id
            next_id += 1
    return [mapping[lab] for lab in new_labels], next_id


MISSION_EQ = "mission_id IS NOT DISTINCT FROM $1"   # mission_id may be NULL


async def _rows(pool, table: str, mission_id: Optional[str], extra: str = "", order: str = "created_at DESC"):
    sharp = "sharpness_score" if table == "person_embeddings" else "NULL::real AS sharpness_score"
    return await pool.fetch(
        f"""SELECT id::text AS id, cluster_id, is_duplicate, encrypted_embedding, {sharp},
                   object_key, drone_id
            FROM {table} WHERE {MISSION_EQ} {extra} ORDER BY {order}""",
        mission_id,
    )


async def _write(pool, table: str, changes: List[Tuple[int, bool, str]]) -> None:
    if changes:
        await pool.executemany(
            f"UPDATE {table} SET cluster_id=$1, is_duplicate=$2 WHERE id=$3::uuid", changes)


async def _identities(kind: str, mission_id: Optional[str], clusters: Dict[int, list]) -> None:
    """person_identities rows for the clusters this run touched (person gallery)."""
    if kind != "person":
        return
    from shared.db import upsert_person_identity
    for cid, (_, count, _, _, obj_key, drone_id) in clusters.items():
        if obj_key:
            await upsert_person_identity(cluster_id=cid, best_image_key=None, drone_id=drone_id,
                                         embedding_count=count, best_person_image_key=obj_key,
                                         mission_id=mission_id)


async def _full(pool, kind: str, table: str, mission_id, next_id: int) -> Tuple[int, int, int]:
    """Re-cluster a whole (small) mission. Returns (embeddings, clusters, next_id)."""
    from person.deduplicator import cluster_embeddings, find_duplicates_in_clusters
    rows = await _rows(pool, table, mission_id)
    vecs, ok = await asyncio.to_thread(_decode, [r["encrypted_embedding"] for r in rows])
    rows = [r for r, good in zip(rows, ok) if good]
    if not rows:
        return 0, 0, next_id
    if len(rows) == 1:
        labels = [0]
    else:
        labels = await asyncio.to_thread(cluster_embeddings, vecs, cfg.REID_DISTANCE_THRESHOLD)
    ids = [r["id"] for r in rows]
    cids, next_id = stable_ids(labels, [r["cluster_id"] for r in rows], next_id)
    sharp = [float(r["sharpness_score"] or 0.0) for r in rows] if kind == "person" else None
    dups, _ = find_duplicates_in_clusters(ids, cids, sharp)
    dup = set(dups)
    changes = [(cid, i in dup, i) for r, i, cid in zip(rows, ids, cids)
               if r["cluster_id"] != cid or bool(r["is_duplicate"]) != (i in dup)]
    await _write(pool, table, changes)

    # Centroid cache (used if the mission grows past REID_CLUSTER_MAX) + identities
    clusters: Dict[int, list] = {}
    for k, (r, cid) in enumerate(zip(rows, cids)):
        c = clusters.setdefault(cid, [np.zeros(vecs.shape[1], np.float32), 0, None, -1.0, None, None])
        c[0] += vecs[k]
        c[1] += 1
        if r["id"] not in dup:
            c[2], c[3], c[4], c[5] = r["id"], float(r["sharpness_score"] or 0.0), r["object_key"], r["drone_id"]
    _centroids[(kind, mission_id)] = clusters
    changed = {cid for cid, _, _ in changes}
    await _identities(kind, mission_id, {cid: c for cid, c in clusters.items() if cid in changed})
    return len(rows), len(clusters), next_id


async def _load_centroids(pool, kind: str, table: str, mission_id) -> Dict[int, list]:
    clusters: Dict[int, list] = {}
    last = ""
    while True:                                  # in chunks: the event loop stays responsive
        rows = await pool.fetch(
            f"""SELECT id::text AS id, cluster_id, is_duplicate, encrypted_embedding,
                       {"sharpness_score" if kind == "person" else "NULL::real AS sharpness_score"},
                       object_key, drone_id
                FROM {table} WHERE {MISSION_EQ} AND cluster_id IS NOT NULL
                  AND id::text > $2 ORDER BY id::text LIMIT {FETCH_CHUNK}""",
            mission_id, last)
        if not rows:
            return clusters
        last = rows[-1]["id"]
        vecs, ok = await asyncio.to_thread(_decode, [r["encrypted_embedding"] for r in rows])
        k = 0
        for r, good in zip(rows, ok):
            if not good:
                continue
            c = clusters.setdefault(r["cluster_id"], [np.zeros(vecs.shape[1], np.float32), 0, None, -1.0, None, None])
            c[0] += vecs[k]
            c[1] += 1
            k += 1
            if not r["is_duplicate"]:
                c[2], c[3], c[4], c[5] = r["id"], float(r["sharpness_score"] or 0.0), r["object_key"], r["drone_id"]


def _assign(vecs: np.ndarray, clusters: Dict[int, list], next_id: int, threshold: float):
    """Nearest-centroid assignment of new embeddings, in order (worker thread)."""
    ids = list(clusters)
    cents = (np.stack([clusters[c][0] / max(np.linalg.norm(clusters[c][0]), 1e-12) for c in ids])
             if ids else np.zeros((0, vecs.shape[1]), np.float32))
    out = []
    for v in vecs:
        if len(ids):
            sims = cents @ v
            j = int(np.argmax(sims))
            if 1.0 - float(sims[j]) < threshold:
                cid = ids[j]
                c = clusters[cid]
                c[0] += v
                c[1] += 1
                cents[j] = c[0] / max(np.linalg.norm(c[0]), 1e-12)
                out.append(cid)
                continue
        cid, next_id = next_id, next_id + 1
        clusters[cid] = [v.copy(), 1, None, -1.0, None, None]
        ids.append(cid)
        cents = np.vstack([cents, v[None, :]])
        out.append(cid)
    return out, next_id


async def _incremental(pool, kind: str, table: str, mission_id, next_id: int) -> Tuple[int, int, int]:
    """Assign a large mission's new embeddings to its clusters. Returns (new, clusters, next_id)."""
    key = (kind, mission_id)
    if key not in _centroids:
        _centroids[key] = await _load_centroids(pool, kind, table, mission_id)
    clusters = _centroids[key]
    rows = await _rows(pool, table, mission_id, "AND cluster_id IS NULL", order="created_at")
    vecs, ok = await asyncio.to_thread(_decode, [r["encrypted_embedding"] for r in rows])
    unreadable = [r for r, good in zip(rows, ok) if not good]
    rows = [r for r, good in zip(rows, ok) if good]
    cids, next_id = await asyncio.to_thread(_assign, vecs, clusters, next_id, cfg.REID_DISTANCE_THRESHOLD)
    changes, touched = [], set()
    for r, cid in zip(rows, cids):
        c = clusters[cid]
        sharp = float(r["sharpness_score"] or 0.0)
        # Shown copy: persons — the sharpest crop; faces — the newest (as in the full run)
        better = c[2] is None or (sharp > c[3] if kind == "person" else True)
        if better:
            if c[2] is not None:
                changes.append((cid, True, c[2]))            # the previous shown copy
            c[2], c[3], c[4], c[5] = r["id"], sharp, r["object_key"], r["drone_id"]
        changes.append((cid, not better, r["id"]))
        touched.add(cid)
    # Rows whose embedding cannot be read get their own cluster so they are not retried forever
    for r in unreadable:
        changes.append((next_id, False, r["id"]))
        next_id += 1
    await _write(pool, table, changes)
    await _identities(kind, mission_id, {cid: clusters[cid] for cid in touched})
    return len(rows), len(clusters), next_id


async def recluster(kind: str) -> Dict[str, int]:
    """One clustering pass over the missions with new embeddings of one kind."""
    from shared.db import get_pool
    table = TABLES[kind]
    pool = await get_pool()
    dirty = await pool.fetch(
        f"""SELECT mission_id, COUNT(*) AS n FROM {table}
            GROUP BY mission_id HAVING bool_or(cluster_id IS NULL)""")
    if not dirty:
        return {}
    next_id = int(await pool.fetchval(f"SELECT COALESCE(MAX(cluster_id), -1) + 1 FROM {table}"))
    done = {}
    for row in dirty:
        mission_id, n = row["mission_id"], int(row["n"])
        try:
            if n <= cfg.REID_CLUSTER_MAX:
                count, clusters, next_id = await _full(pool, kind, table, mission_id, next_id)
                how = "full"
            else:
                count, clusters, next_id = await _incremental(pool, kind, table, mission_id, next_id)
                how = "incremental"
            done[mission_id or "-"] = clusters
            log.info("%s Re-ID clustering (%s) mission %s: %d embeddings → %d clusters",
                     kind.capitalize(), how, mission_id or "-", count, clusters)
        except Exception as exc:
            log.warning("%s Re-ID clustering failed for mission %s: %s", kind, mission_id, exc)
    return done
