"""Images of duplicate crops are deleted once their mission is quiet (person/dup_images.py):
only duplicates, only quiet and fully clustered missions, never an image an identity or a
shown row still names; rows and embeddings stay, and a re-cluster never picks a row without
an image as the shown copy while its cluster has one.

Needs a real Postgres (skipped otherwise), e.g. TEST_PG_DSN=postgresql://postgres@127.0.0.1:55432/postgres
Run from dvas_analytics/:  python -m pytest person/tests/test_dup_images.py -q
"""
import asyncio
import os
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")

from person import dup_images
from person.deduplicator import find_duplicates_in_clusters, get_identity_representatives
from shared.config import cfg

DSN = os.getenv("TEST_PG_DSN", "postgresql://postgres@127.0.0.1:55432/postgres")

SCHEMA = """
DROP TABLE IF EXISTS person_embeddings, face_embeddings, person_identities;
CREATE TABLE person_embeddings (id uuid PRIMARY KEY, cluster_id int, is_duplicate bool DEFAULT false,
  sharpness_score real, image_path text, object_key text NOT NULL, mission_id text,
  created_at timestamptz DEFAULT clock_timestamp());
CREATE TABLE face_embeddings (id uuid PRIMARY KEY, cluster_id int, is_duplicate bool DEFAULT false,
  image_path text, object_key text, person_object_key text, mission_id text,
  created_at timestamptz DEFAULT clock_timestamp());
CREATE TABLE person_identities (identity_id uuid PRIMARY KEY, cluster_id int NOT NULL, best_image_key text,
  best_person_image_key text, mission_id text);
"""


def test_shown_copy_is_one_with_an_image():
    ids = ["a", "b", "c"]
    # b is the sharpest but its image is gone: c (next sharpest with an image) is shown
    dups, n = find_duplicates_in_clusters(ids, [0, 0, 0], [0.1, 0.9, 0.5], [True, False, True])
    assert n == 1 and set(dups) == {"a", "b"}
    assert get_identity_representatives(ids, [0, 0, 0], [0.1, 0.9, 0.5], [True, False, True]) == {0: "c"}
    # faces (no sharpness): order kept among rows with an image
    dups, _ = find_duplicates_in_clusters(ids, [0, 0, 0], None, [False, True, True])
    assert set(dups) == {"a", "c"}
    # nobody has an image: unchanged choice
    assert get_identity_representatives(ids, [0, 0, 0], [0.1, 0.9, 0.5], [False] * 3) == {0: "b"}


def _reachable():
    async def go():
        c = await asyncpg.connect(DSN, timeout=2)
        await c.close()
    try:
        asyncio.run(go())
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _reachable(), reason=f"no Postgres at {DSN}")


def run(fn):
    async def go():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        await pool.execute(SCHEMA)
        try:
            return await fn(pool)
        finally:
            await pool.close()
    return asyncio.run(go())


async def add(pool, table, mission, cluster, dup, age_s=7200, key=None, person_key=None):
    rid = uuid.uuid4()
    key = key or f"k/{rid}"
    if table == "person_embeddings":
        await pool.execute(
            "INSERT INTO person_embeddings (id, cluster_id, is_duplicate, image_path, object_key, mission_id, created_at)"
            " VALUES ($1, $2, $3, $4, $4, $5, now() - make_interval(secs => $6))",
            rid, cluster, dup, key, mission, float(age_s))
    else:
        await pool.execute(
            "INSERT INTO face_embeddings (id, cluster_id, is_duplicate, image_path, object_key, person_object_key,"
            " mission_id, created_at) VALUES ($1, $2, $3, $4, $4, $5, $6, now() - make_interval(secs => $7))",
            rid, cluster, dup, key, person_key, mission, float(age_s))
    return str(rid), key


class Deleter:
    def __init__(self, fail=()):
        self.deleted, self.fail = [], set(fail)

    async def __call__(self, bucket, key):
        assert bucket == cfg.BUCKET_FACES
        if key in self.fail:
            return False
        self.deleted.append(key)
        return True


@pg
def test_deletes_only_duplicates_of_quiet_clustered_missions(monkeypatch):
    monkeypatch.setattr(cfg, "DUP_PURGE_AFTER_S", 3600.0)

    async def t(pool):
        T = "person_embeddings"
        _, shown = await add(pool, T, "M1", 0, False)
        d1, k1 = await add(pool, T, "M1", 0, True)
        d2, k2 = await add(pool, T, "M1", 0, True)
        _, recent = await add(pool, "person_embeddings", "M2", 0, True)          # M2 still active
        await add(pool, T, "M2", 0, False, age_s=60)
        _, unclustered = await add(pool, T, "M3", 0, True)                     # M3 not all clustered
        await add(pool, T, "M3", None, False)
        dl = Deleter()
        out = await dup_images.purge_once(pool, dl)
        assert sorted(dl.deleted) == sorted([k1, k2]) and out[T] == 2
        rows = {str(r["id"]): r for r in await pool.fetch(f"SELECT * FROM {T}")}
        assert rows[d1]["object_key"] == "" and rows[d1]["image_path"] is None    # row kept, image gone
        assert await pool.fetchval(f"SELECT count(*) FROM {T}") == 7
        assert await pool.fetchval(f"SELECT count(*) FROM {T} WHERE object_key = $1", shown) == 1
        # a second pass finds nothing more
        assert (await dup_images.purge_once(pool, Deleter()))[T] == 0
        return recent, unclustered
    run(t)


@pg
def test_never_deletes_an_image_still_in_use(monkeypatch):
    monkeypatch.setattr(cfg, "DUP_PURGE_AFTER_S", 3600.0)

    async def t(pool):
        T = "face_embeddings"
        await add(pool, T, "M1", 0, False, key="shared")
        await add(pool, T, "M1", 0, True, key="shared")                          # same key as the shown row
        await add(pool, T, "M1", 1, False)
        _, idk = await add(pool, T, "M1", 1, True)
        await pool.execute("INSERT INTO person_identities VALUES ($1, 1, $2, NULL, 'M1')", uuid.uuid4(), idk)
        _, k, = await add(pool, T, "M1", 1, True, person_key="p/1")
        dl = Deleter()
        await dup_images.purge_once(pool, dl)
        assert sorted(dl.deleted) == sorted([k, "p/1"])
        assert await pool.fetchval(f"SELECT count(*) FROM {T} WHERE person_object_key IS NOT NULL") == 0
    run(t)


@pg
def test_failed_delete_is_logged_and_the_pass_goes_on(monkeypatch):
    monkeypatch.setattr(cfg, "DUP_PURGE_AFTER_S", 3600.0)
    monkeypatch.setattr(dup_images, "BATCH", 2)

    async def t(pool):
        T = "person_embeddings"
        await add(pool, T, "M1", 0, False)
        keys = [(await add(pool, T, "M1", 0, True))[1] for _ in range(5)]
        dl = Deleter(fail={keys[0]})
        out = await dup_images.purge_once(pool, dl)
        assert out[T] == 4 and sorted(dl.deleted) == sorted(keys[1:])
        assert await pool.fetchval(f"SELECT count(*) FROM {T} WHERE is_duplicate AND object_key <> ''") == 0
    run(t)
