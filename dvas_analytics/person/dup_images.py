# -*- coding: utf-8 -*-
"""
Delete the images of duplicate person / face crops once a mission is quiet.

Found on the pod (16 Nov): Re-ID dedup (person/reid.py) only marked rows is_duplicate; every
crop stayed in MinIO, so dvas-faces grew to 24 GB and filled the volume. A cluster shows one
image (persons: the sharpest crop, faces: the newest); the duplicates' images are never shown
in the gallery or the identities.

Every DUP_PURGE_EVERY_S, for each mission whose newest embedding is older than
DUP_PURGE_AFTER_S and whose embeddings are all clustered:
  * duplicate rows that still have an image get their keys cleared (only while they are still
    duplicates — a re-cluster in between wins), then the images are deleted from MinIO;
  * the row and its embedding stay: clustering, dedup counts and reverse face search still
    use them (a match shows its cluster's image — person/main.py);
  * an image still named by an identity (person_identities) or by a row that is not a
    duplicate is never deleted.
Watchlist alerts and confirmed-event evidence use their own uploads (alerts/, the evidence
vault), not these crops, so evidence is untouched.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from shared.config import cfg

log = logging.getLogger(__name__)

BATCH = 500                # rows claimed per query
DELETE_CONCURRENCY = 16    # MinIO deletes in flight

# person_embeddings.object_key is NOT NULL: a deleted image is '' there, NULL in face_embeddings
_CLEARED = {"person_embeddings": "object_key = '', image_path = NULL",
            "face_embeddings": "object_key = NULL, person_object_key = NULL, image_path = NULL"}
_KEYS = {"person_embeddings": ("object_key",), "face_embeddings": ("object_key", "person_object_key")}


def _has_image(col: str = "object_key") -> str:
    return f"COALESCE({col}, '') <> ''"


async def _quiet_missions(pool, table: str, after_s: float) -> List[Optional[str]]:
    rows = await pool.fetch(
        f"""SELECT mission_id FROM {table} GROUP BY mission_id
            HAVING max(created_at) < now() - make_interval(secs => $1)
               AND bool_and(cluster_id IS NOT NULL)
               AND bool_or(is_duplicate AND {_has_image()})""",
        float(after_s))
    return [r["mission_id"] for r in rows]


async def _referenced(pool, keys: List[str]) -> set:
    """The keys an identity or a shown (non-duplicate) row still names."""
    rows = await pool.fetch(
        f"""SELECT best_image_key AS k FROM person_identities WHERE best_image_key = ANY($1::text[])
            UNION SELECT best_person_image_key FROM person_identities WHERE best_person_image_key = ANY($1::text[])
            UNION SELECT object_key FROM person_embeddings WHERE NOT is_duplicate AND object_key = ANY($1::text[])
            UNION SELECT object_key FROM face_embeddings WHERE NOT is_duplicate AND object_key = ANY($1::text[])""",
        keys)
    return {r["k"] for r in rows if r["k"]}


async def _purge_mission(pool, table: str, mission_id: Optional[str], delete) -> int:
    cols = _KEYS[table]
    deleted = 0
    while True:
        cand = await pool.fetch(
            f"""SELECT id, {", ".join(cols)} FROM {table}
                WHERE mission_id IS NOT DISTINCT FROM $1 AND is_duplicate AND {_has_image()}
                LIMIT {BATCH}""",
            mission_id)
        if not cand:
            return deleted
        # Clear the keys first, only of rows that are still duplicates; delete what was cleared
        cleared = await pool.fetch(
            f"""WITH c AS (SELECT id, {", ".join(cols)} FROM {table}
                           WHERE id = ANY($1::uuid[]) AND is_duplicate FOR UPDATE)
                UPDATE {table} t SET {_CLEARED[table]} FROM c WHERE t.id = c.id
                RETURNING {", ".join(f"c.{k}" for k in cols)}""",
            [r["id"] for r in cand])
        keys = sorted({r[k] for r in cleared for k in cols if r[k]})
        keep = await _referenced(pool, keys) if keys else set()
        sem = asyncio.Semaphore(DELETE_CONCURRENCY)

        async def one(key: str) -> bool:
            async with sem:
                return bool(await delete(cfg.BUCKET_FACES, key))
        ok = await asyncio.gather(*(one(k) for k in keys if k not in keep))
        deleted += sum(ok)
        if len(ok) > sum(ok):
            log.warning("Duplicate images %s mission %s: %d MinIO deletes failed (left as orphans)",
                        table, mission_id or "-", len(ok) - sum(ok))
        if len(cand) < BATCH:
            return deleted


async def purge_once(pool=None, delete=None) -> Dict[str, int]:
    """One pass over the quiet missions of both tables. Returns {table: images deleted}."""
    from shared import minio_client
    from shared.db import get_pool
    pool = pool or await get_pool()
    delete = delete or minio_client.delete_object
    out: Dict[str, int] = {}
    for table in ("person_embeddings", "face_embeddings"):
        n = 0
        for mission_id in await _quiet_missions(pool, table, cfg.DUP_PURGE_AFTER_S):
            try:
                m = await _purge_mission(pool, table, mission_id, delete)
            except Exception as exc:
                log.warning("Duplicate image purge failed for %s mission %s: %s", table, mission_id, exc)
                continue
            if m:
                log.info("Deleted %d duplicate images of %s, mission %s", m, table, mission_id or "-")
            n += m
        out[table] = n
    return out


async def loop() -> None:
    """Background task (person service startup)."""
    if not cfg.DUP_PURGE:
        log.info("Duplicate image purge off (DVAS_PURGE_DUP_IMAGES=0)")
        return
    log.info("Duplicate image purge: missions quiet for %.0f s, every %.0f s",
             cfg.DUP_PURGE_AFTER_S, cfg.DUP_PURGE_EVERY_S)
    while True:
        await asyncio.sleep(cfg.DUP_PURGE_EVERY_S)
        try:
            await purge_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Duplicate image purge pass failed: %s", exc)
