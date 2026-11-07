"""Re-ID clustering per mission on a timer (person/reid.py): same people → same clusters,
ids stable between runs and unique across missions, duplicates marked, identities not
multiplied; large missions assign new embeddings incrementally.

Needs a real Postgres (skipped otherwise), e.g. TEST_PG_DSN=postgresql://postgres@127.0.0.1:55432/postgres
Run from dvas_analytics/:  python -m pytest person/tests/test_reid_clustering.py -q
"""
import asyncio
import os
import uuid

import numpy as np
import pytest

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("sklearn")

from person import reid
from shared.config import cfg
from shared.crypto import encrypt_vector

DSN = os.getenv("TEST_PG_DSN", "postgresql://postgres@127.0.0.1:55432/postgres")

SCHEMA = """
DROP TABLE IF EXISTS person_embeddings, face_embeddings, person_identities;
CREATE TABLE person_embeddings (id uuid PRIMARY KEY, cluster_id int, is_duplicate bool DEFAULT false,
  encrypted_embedding bytea, sharpness_score real, object_key text, drone_id text, mission_id text,
  created_at timestamptz DEFAULT clock_timestamp());
CREATE TABLE face_embeddings (id uuid PRIMARY KEY, cluster_id int, is_duplicate bool DEFAULT false,
  encrypted_embedding bytea, object_key text, drone_id text, mission_id text,
  created_at timestamptz DEFAULT clock_timestamp());
CREATE TABLE person_identities (identity_id uuid PRIMARY KEY, cluster_id int NOT NULL, best_image_key text,
  best_person_image_key text, drone_id text, embedding_count int, mission_id text, last_seen timestamptz DEFAULT now());
"""


def _reachable():
    async def go():
        c = await asyncpg.connect(DSN, timeout=2)
        await c.close()
    try:
        asyncio.run(go())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no Postgres at {DSN}")

RNG = np.random.default_rng(1)
PEOPLE = [RNG.normal(size=2048) for _ in range(4)]


def look(person, noise=0.05):
    v = PEOPLE[person] + RNG.normal(size=2048) * noise * np.linalg.norm(PEOPLE[person]) / np.sqrt(2048)
    return (v / np.linalg.norm(v)).tolist()


def run(fn):
    reid._centroids.clear()

    async def go():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        await pool.execute(SCHEMA)
        import shared.db as sdb

        async def get_pool():
            return pool
        orig = sdb.get_pool
        sdb.get_pool = get_pool
        try:
            return await fn(pool)
        finally:
            sdb.get_pool = orig
            await pool.close()
    return asyncio.run(go())


async def add(pool, mission, person, sharp=1.0, table="person_embeddings"):
    rid = str(uuid.uuid4())
    if table == "person_embeddings":
        await pool.execute(
            "INSERT INTO person_embeddings (id, encrypted_embedding, sharpness_score, object_key, drone_id, mission_id)"
            " VALUES ($1, $2, $3, $4, 'D', $5)", uuid.UUID(rid), encrypt_vector(look(person)), sharp, f"k/{rid}", mission)
    else:
        await pool.execute(
            "INSERT INTO face_embeddings (id, encrypted_embedding, object_key, drone_id, mission_id)"
            " VALUES ($1, $2, $3, 'D', $4)", uuid.UUID(rid), encrypt_vector(look(person)), f"k/{rid}", mission)
    return rid


async def labels(pool, table="person_embeddings"):
    rows = await pool.fetch(f"SELECT id::text, cluster_id, is_duplicate, mission_id FROM {table}")
    return {r["id"]: (r["cluster_id"], r["is_duplicate"], r["mission_id"]) for r in rows}


def test_stable_ids_keep_old_ids_and_number_new_ones_after_the_max():
    ids, nxt = reid.stable_ids([0, 0, 1, 1, 2], [7, 7, 7, None, None], 10)
    assert ids == [7, 7, 10, 10, 11] and nxt == 12


def test_full_run_clusters_people_and_keeps_ids_stable():
    async def t(pool):
        a = [await add(pool, "M1", 0, sharp=s) for s in (1, 5, 2)]
        b = [await add(pool, "M1", 1, sharp=s) for s in (3, 1)]
        assert (await reid.recluster("person")) == {"M1": 2}
        lab = await labels(pool)
        assert len({lab[x][0] for x in a}) == 1 and len({lab[x][0] for x in b}) == 1
        assert lab[a[0]][0] != lab[b[0]][0]
        shown = [x for x in a if not lab[x][1]]
        assert shown == [a[1]]                                # the sharpest crop is shown
        assert await pool.fetchval("SELECT count(*) FROM person_identities") == 2
        assert await reid.recluster("person") == {}           # nothing new → no work
        a.append(await add(pool, "M1", 0, sharp=0.5))
        await reid.recluster("person")
        lab2 = await labels(pool)
        assert all(lab2[x][0] == lab[a[0]][0] for x in a)    # same id as before
        assert lab2[b[0]][0] == lab[b[0]][0]
        assert await pool.fetchval("SELECT count(*) FROM person_identities") == 2
    run(t)


def test_ids_are_unique_across_missions():
    async def t(pool):
        await add(pool, "M1", 0)
        await add(pool, "M1", 1)
        await reid.recluster("person")
        await add(pool, "M2", 0)
        await add(pool, None, 2)
        await reid.recluster("person")
        lab = await labels(pool)
        per_mission = {}
        for cid, _, m in lab.values():
            per_mission.setdefault(m, set()).add(cid)
        assert len(per_mission["M1"] | per_mission["M2"] | per_mission[None]) == 4
    run(t)


def test_large_mission_assigns_new_embeddings_incrementally(monkeypatch):
    monkeypatch.setattr(cfg, "REID_CLUSTER_MAX", 4)
    async def t(pool):
        first = [await add(pool, "M1", p) for p in (0, 0, 1)]
        await reid.recluster("person")                        # 3 rows: full run
        before = await labels(pool)
        reid._centroids.clear()                               # as after a service restart
        new0 = await add(pool, "M1", 0, sharp=9)              # sharper look at person 0
        new3 = await add(pool, "M1", 3)                       # someone new
        await add(pool, "M1", 3)
        await reid.recluster("person")                        # 6 rows > 4: incremental
        lab = await labels(pool)
        assert lab[new0][0] == before[first[0]][0] and not lab[new0][1]
        assert lab[first[0]][1] and lab[first[1]][1]          # the older copies are duplicates now
        assert lab[new3][0] > max(c for c, _, _ in before.values())
        assert await pool.fetchval("SELECT count(*) FROM person_embeddings WHERE cluster_id IS NULL") == 0
        assert await pool.fetchval("SELECT count(*) FROM person_identities") == 3
    run(t)


def test_faces_keep_the_newest_copy():
    async def t(pool):
        f = [await add(pool, "M1", 2, table="face_embeddings") for _ in range(3)]
        await reid.recluster("face")
        lab = await labels(pool, "face_embeddings")
        assert len({lab[x][0] for x in f}) == 1
        assert [x for x in f if not lab[x][1]] == [f[-1]]
        assert await pool.fetchval("SELECT count(*) FROM person_identities") == 0
    run(t)
