"""Phase 2a — mission-keyed streams (plan §2.D D2/D3/D10), against a real Valkey.

    docker run --rm -d --name dvas-valkey-2a -p 127.0.0.1:6391:6379 valkey/valkey:7.2
    cd dvas_ingest/ingest && python -m pytest tests -q

The integration tests are skipped when Valkey is not reachable (DVAS_TEST_VALKEY_URL,
default redis://127.0.0.1:6391/1). The database is flushed before every test.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import main  # noqa: E402
import queue_manager as qm  # noqa: E402

VALKEY_URL = os.getenv("DVAS_TEST_VALKEY_URL", "redis://127.0.0.1:6391/1")
JPEG_A = b"\xff\xd8\xff\xe0" + b"A" * 64
JPEG_B = b"\xff\xd8\xff\xe0" + b"B" * 64


def _valkey():
    import redis
    try:
        r = redis.Redis.from_url(VALKEY_URL, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception:
        return None


def s(v):
    return v.decode() if isinstance(v, bytes) else v


def H(sub="op-a", roles="drone-operator", user=None):
    return {"x-test-sub": sub, "x-test-roles": roles, "x-test-user": user or sub}


def _claims(request: "main.Request"):
    return {"sub": request.headers.get("x-test-sub", "op-a"),
            "preferred_username": request.headers.get("x-test-user", "op-a"),
            "realm_access": {"roles": request.headers.get("x-test-roles", "drone-operator").split(",")}}


def _reset_main_state():
    for d in (main._active_missions, main._drain_tasks, main._ingest_stats, main._rejections,
              main._latest_metadata, main._mission_drain_tasks, main._drone_active_sid,
              main._reject_log_at):
        d.clear()


@pytest.fixture
def valkey():
    r = _valkey()
    if r is None:
        pytest.skip(f"Valkey not reachable at {VALKEY_URL}")
    r.flushdb()
    yield r
    r.flushdb()


def _make_client(monkeypatch, valkey, mode):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(qm, "REDIS_URL", VALKEY_URL)
    monkeypatch.setattr(qm, "STREAM_KEY_MODE", mode)
    monkeypatch.setattr(qm, "_manager", None)
    monkeypatch.setattr(main, "DRAIN_MIN_S", 0.0)
    monkeypatch.setattr(main, "ORPHAN_IDLE_S", 0.0)
    monkeypatch.setattr(main, "KNOWN_DRONES", ["DRONE_KL_001"])
    events = []

    async def record(event):
        events.append(event)
    monkeypatch.setattr(main.ws_manager, "broadcast", record)
    _reset_main_state()
    main.app.dependency_overrides[main.validate_token] = _claims
    main.app.dependency_overrides[main.validate_token_flexible] = _claims
    tc = TestClient(main.app, raise_server_exceptions=False)
    tc.events = events
    return tc


@pytest.fixture
def client(monkeypatch, valkey):
    tc = _make_client(monkeypatch, valkey, "mission")
    try:
        with tc:
            yield tc
    finally:
        main.app.dependency_overrides.clear()
        _reset_main_state()
        main._qm = None


@pytest.fixture
def drone_client(monkeypatch, valkey):
    tc = _make_client(monkeypatch, valkey, "drone")
    try:
        with tc:
            yield tc
    finally:
        main.app.dependency_overrides.clear()
        _reset_main_state()
        main._qm = None


def start(c, drone, mission, **kw):
    return c.post("/ingest/mission/start", json={"drone_id": drone, "mission_id": mission}, headers=H(**kw))


def end(c, drone, mission, **kw):
    return c.post("/ingest/mission/end", json={"drone_id": drone, "mission_id": mission}, headers=H(**kw))


def frame(c, drone, mission, seq=1, jpeg=JPEG_A, **kw):
    meta = {"drone_id": drone, "mission_id": mission, "timestamp": "2026-11-07T10:00:00Z",
            "frame_sequence": seq, "change_score": 0.5}
    return c.post("/ingest/frame", files={"frame": ("f.jpg", jpeg, "image/jpeg")},
                  data={"metadata_json": json.dumps(meta)}, headers=H(**kw))


def consume_all(r, key, group="person-group"):
    """An analyzer that has read and acknowledged every entry of the stream."""
    try:
        r.xgroup_create(key, group, id="0")
    except Exception:
        pass
    for start_id in ("0", ">"):          # entries already delivered but not acked, then new ones
        got = r.xreadgroup(group, "c1", {key: start_id}, count=10_000)
        for _k, entries in got or []:
            if entries:
                r.xack(key, group, *[e[0] for e in entries])


def finalize(c, sid, min_age=0.0, idle=0.0):
    return c.portal.call(main._try_finalize_mission, sid, min_age, idle)


def stop_drain(c, sid):
    def _stop():
        t = main._mission_drain_tasks.pop(sid, None)
        if t:
            t.cancel()
    c.portal.call(_stop)


def idx(r, sid):
    return {s(k): s(v) for k, v in r.hgetall(f"mission:index:{sid}").items()}


def err(resp):
    d = resp.json().get("detail")
    return d.get("error") if isinstance(d, dict) else d


# ── start → frames → one stream per mission ──────────────────────────────────

def test_start_and_frames_go_to_the_missions_own_stream(client, valkey):
    mission = "MSN-20261107-0001-59-KOCHI CITY"          # space in the mission id
    sid = "MSN-20261107-0001-59-KOCHI_CITY"
    assert qm.mission_sid(mission) == sid
    assert valkey.keys("drone:frames:*") == []           # nothing pre-declared in mission mode

    r = start(client, "DRN-010", mission)
    assert r.status_code == 201, r.text
    for seq in (1, 2, 3):
        r = frame(client, "DRN-010", mission, seq)
        assert r.status_code == 202, r.text
        assert r.json()["queue_name"] == f"mission:frames:{sid}"

    key = f"mission:frames:{sid}"
    assert valkey.xlen(key) == 3
    entry = valkey.xrange(key, count=1)[0][1]
    assert s(entry[b"mission_id"]) == mission and s(entry[b"drone_id"]) == "DRN-010"
    assert s(entry[b"frame_seq"]) == "1" and b"frame" in entry and b"metadata" in entry
    assert valkey.xinfo_groups(key) == []                 # no placeholder "analytics" group

    i = idx(valkey, sid)
    assert i["drone_id"] == "DRN-010" and i["mission_id"] == mission and i["operator_sub"] == "op-a"
    assert i["status"] == "live" and i["ended_at"] == "" and float(i["started_at"]) > 0
    assert [s(m) for m in valkey.zrange("drone:missions:DRN-010", 0, -1)] == [sid]
    assert valkey.zscore("drone:missions:DRN-010", sid) == pytest.approx(float(i["started_at"]))
    assert s(valkey.get("drone:active_mission:DRN-010")) == sid
    assert valkey.ttl(f"mission:index:{sid}") == -1


def test_frame_before_start_is_409(client, valkey):
    r = frame(client, "DRN-010", "MSN-NEVER")
    assert r.status_code == 409 and err(r) == "mission_not_started"
    assert valkey.keys("mission:frames:*") == []


def test_foreign_operator_frame_403_and_end_rules(client, valkey):
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    r = frame(client, "DRN-010", "MSN-1", sub="op-b")
    assert r.status_code == 403 and err(r) == "not_mission_operator"
    r = frame(client, "DRN-010", "MSN-1", sub="op-b", roles="sys-admin")   # role is not ownership
    assert r.status_code == 403
    assert valkey.xlen("mission:frames:MSN-1") == 0

    r = end(client, "DRN-010", "MSN-1", sub="op-b", roles="control-room")
    assert r.status_code == 403
    r = end(client, "DRN-010", "MSN-1", sub="op-b", roles="supervisor")
    assert r.status_code == 200 and r.json()["status"] == "draining"
    assert idx(valkey, "MSN-1")["status"] in ("draining", "ended")


def test_end_by_operator_and_service_account(client, valkey):
    assert start(client, "DRN-1", "MSN-1").status_code == 201
    assert end(client, "DRN-1", "MSN-1").status_code == 200
    assert start(client, "DRN-2", "MSN-2").status_code == 201
    r = end(client, "DRN-2", "MSN-2", sub="svc", roles="", user="service-account-dvas-backend")
    assert r.status_code == 200, r.text
    r = end(client, "DRN-2", "MSN-NOPE")
    assert r.status_code == 409 and err(r) == "mission_not_started"


def test_wrong_drone_is_409(client, valkey):
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    r = frame(client, "DRN-011", "MSN-1")
    assert r.status_code == 409 and err(r) == "drone_mismatch"
    assert valkey.exists("mission:frames:MSN-1") == 0


def test_two_operators_cannot_share_a_drone_or_a_mission_id(client, valkey):
    assert start(client, "DRN-010", "MSN-A").status_code == 201
    started = idx(valkey, "MSN-A")["started_at"]

    r = start(client, "DRN-010", "MSN-B", sub="op-b")          # same drone id, other flight
    assert r.status_code == 409 and err(r) == "drone_in_use"
    r = start(client, "DRN-099", "MSN-A", sub="op-b")          # mission id of another operator
    assert r.status_code == 409 and err(r) == "mission_id_taken"
    r = start(client, "DRN-099", "MSN-A")                       # same operator, other drone
    assert r.status_code == 409 and err(r) == "mission_id_taken"
    assert valkey.exists("mission:index:MSN-B") == 0

    r = start(client, "DRN-010", "MSN-A")                       # re-sent start: idempotent
    assert r.status_code == 201
    assert idx(valkey, "MSN-A")["started_at"] == started
    assert idx(valkey, "MSN-A")["status"] == "live"

    # still blocked while MSN-A only drains (one unread frame keeps it draining)
    assert frame(client, "DRN-010", "MSN-A").status_code == 202
    assert end(client, "DRN-010", "MSN-A").status_code == 200
    stop_drain(client, "MSN-A")
    r = start(client, "DRN-010", "MSN-B", sub="op-b")
    assert r.status_code == 409 and err(r) == "drone_in_use"


def test_same_operator_new_mission_supersedes_the_live_one(client, valkey):
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    assert frame(client, "DRN-010", "MSN-1", 1).status_code == 202
    assert start(client, "DRN-010", "MSN-2").status_code == 201
    stop_drain(client, "MSN-1")

    i1 = idx(valkey, "MSN-1")
    assert i1["status"] == "draining" and float(i1["ended_at"]) > 0
    assert idx(valkey, "MSN-2")["status"] == "live"
    assert s(valkey.get("drone:active_mission:DRN-010")) == "MSN-2"
    assert [s(m) for m in valkey.zrange("drone:missions:DRN-010", 0, -1)] == ["MSN-1", "MSN-2"]

    assert frame(client, "DRN-010", "MSN-2", 1).status_code == 202
    assert frame(client, "DRN-010", "MSN-1", 2).status_code == 202     # in-flight retry of MSN-1
    assert valkey.xlen("mission:frames:MSN-1") == 2
    assert valkey.xlen("mission:frames:MSN-2") == 1
    assert main._latest_metadata["DRN-010"]["mission_id"] == "MSN-2"   # telemetry stays on MSN-2


def test_late_retry_within_grace_lands_in_the_ended_missions_stream(client, valkey):
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    assert frame(client, "DRN-010", "MSN-1", 1).status_code == 202
    assert start(client, "DRN-010", "MSN-2").status_code == 201        # MSN-1 → draining
    stop_drain(client, "MSN-1")
    assert frame(client, "DRN-010", "MSN-2", 1).status_code == 202

    consume_all(valkey, "mission:frames:MSN-1")
    assert finalize(client, "MSN-1") == "finalized"
    assert valkey.exists("mission:frames:MSN-1") == 0
    assert idx(valkey, "MSN-1")["status"] == "ended"
    assert [s(m) for m in valkey.zrange("drone:missions:DRN-010", 0, -1)] == ["MSN-2"]
    assert s(valkey.get("drone:active_mission:DRN-010")) == "MSN-2"     # not MSN-1's pointer
    drained = [e for e in client.events if e.get("event") == "mission_drained"]
    assert drained[-1]["drone_id"] == "DRN-010" and drained[-1]["mission_id"] == "MSN-1"
    assert drained[-1]["drone_still_active"] is True

    r = frame(client, "DRN-010", "MSN-1", 2)                            # late retry, within grace
    assert r.status_code == 202, r.text
    assert r.json()["queue_name"] == "mission:frames:MSN-1"
    assert valkey.xlen("mission:frames:MSN-1") == 1
    assert valkey.xlen("mission:frames:MSN-2") == 1                     # never the other flight's
    assert valkey.zscore("drone:missions:DRN-010", "MSN-1") is not None  # listed again for analytics

    valkey.hset("mission:index:MSN-1", "ended_at", repr(time.time() - qm.MISSION_GRACE_S - 5))
    r = frame(client, "DRN-010", "MSN-1", 3)
    assert r.status_code == 409 and err(r) == "mission_ended"
    assert valkey.xlen("mission:frames:MSN-1") == 1

    # the re-created stream is cleaned up once read (ended mission: idle + consumed)
    assert client.portal.call(main._mission_cleanup_pass) is None
    assert valkey.exists("mission:frames:MSN-1") == 1                   # nobody read it yet
    consume_all(valkey, "mission:frames:MSN-1")
    client.portal.call(main._mission_cleanup_pass)
    assert valkey.exists("mission:frames:MSN-1") == 0
    assert valkey.zscore("drone:missions:DRN-010", "MSN-1") is None


# ── drain ─────────────────────────────────────────────────────────────────────

def test_drain_purges_only_when_every_analyzer_read_everything(client, valkey):
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    for seq in range(1, 6):
        assert frame(client, "DRN-010", "MSN-1", seq).status_code == 202
    key = "mission:frames:MSN-1"
    valkey.xgroup_create(key, "person-group", id="0")
    valkey.xreadgroup("person-group", "c1", {key: ">"}, count=2)       # read 2 of 5

    assert finalize(client, "MSN-1") == "live"                          # live: never purged
    assert end(client, "DRN-010", "MSN-1").status_code == 200
    stop_drain(client, "MSN-1")
    assert finalize(client, "MSN-1") is None                            # 3 unread, 2 pending
    assert valkey.xlen(key) == 5 and idx(valkey, "MSN-1")["status"] == "draining"

    r = client.get("/ingest/streams/active")
    d = r.json()["drones"][0]
    assert d["status"] == "draining" and d["messages_ready"] == 5        # 2 pending + 3 undelivered

    consume_all(valkey, key)
    assert finalize(client, "MSN-1", min_age=3600) is None              # grace not over yet
    assert finalize(client, "MSN-1") == "finalized"
    assert valkey.exists(key) == 0
    i = idx(valkey, "MSN-1")
    assert i["status"] == "ended" and float(i["ended_at"]) > 0
    assert 0 < valkey.ttl("mission:index:MSN-1") <= 7 * 24 * 3600
    assert valkey.exists("drone:active_mission:DRN-010") == 0
    assert valkey.exists("drone:missions:DRN-010") == 0
    assert "DRN-010" not in main._active_missions
    ev = [e for e in client.events if e.get("event") == "mission_drained"][-1]
    assert ev == {"event": "mission_drained", "drone_id": "DRN-010", "mission_id": "MSN-1",
                  "drone_still_active": False}
    assert client.get("/ingest/streams/active").json()["count"] == 0


def test_switched_off_analyzer_does_not_hold_the_drain(client, valkey, monkeypatch):
    # crowd read part of the mission once and was switched off; person read everything
    assert start(client, "DRN-012", "MSN-1").status_code == 201
    for seq in range(1, 4):
        assert frame(client, "DRN-012", "MSN-1", seq).status_code == 202
    key = "mission:frames:MSN-1"
    valkey.xgroup_create(key, "crowd-group", id="0")
    valkey.xreadgroup("crowd-group", "crowd-analyzer", {key: ">"}, count=1)
    valkey.xack(key, "crowd-group", *[e[0] for e in valkey.xrange(key, count=1)])
    assert end(client, "DRN-012", "MSN-1").status_code == 200
    stop_drain(client, "MSN-1")
    consume_all(valkey, key)                 # person-group (fresh consumer)
    assert finalize(client, "MSN-1") is None                            # crowd still counts
    monkeypatch.setattr(qm, "QUEUE_READER_IDLE_S", 0.2)
    import time as _t
    _t.sleep(0.3)
    valkey.xreadgroup("person-group", "c1", {key: ">"}, count=1)       # person polls on
    assert finalize(client, "MSN-1") == "finalized"
    assert valkey.exists(key) == 0


def test_cleanup_pass_drains_after_restart_and_skips_live(client, valkey):
    # a draining mission without a monitor (ingest restarted) and a live one, both read fully
    assert start(client, "DRN-1", "MSN-1").status_code == 201
    assert frame(client, "DRN-1", "MSN-1").status_code == 202
    assert end(client, "DRN-1", "MSN-1").status_code == 200
    stop_drain(client, "MSN-1")
    assert start(client, "DRN-2", "MSN-2", sub="op-b").status_code == 201
    assert frame(client, "DRN-2", "MSN-2", sub="op-b").status_code == 202
    consume_all(valkey, "mission:frames:MSN-1")
    consume_all(valkey, "mission:frames:MSN-2")

    client.portal.call(main._mission_cleanup_pass)
    assert valkey.exists("mission:frames:MSN-1") == 0
    assert idx(valkey, "MSN-1")["status"] == "ended"
    assert valkey.exists("mission:frames:MSN-2") == 1                   # live: kept
    assert idx(valkey, "MSN-2")["status"] == "live"


def test_drain_monitor_started_by_mission_end(client, valkey):
    assert start(client, "DRN-1", "MSN-1").status_code == 201
    assert frame(client, "DRN-1", "MSN-1").status_code == 202
    consume_all(valkey, "mission:frames:MSN-1")
    assert end(client, "DRN-1", "MSN-1").status_code == 200             # DRAIN_MIN_S = 0 here
    deadline = time.time() + 5
    while time.time() < deadline and valkey.exists("mission:frames:MSN-1"):
        time.sleep(0.1)
    assert valkey.exists("mission:frames:MSN-1") == 0
    assert idx(valkey, "MSN-1")["status"] == "ended"


# ── drone-keyed endpoints ─────────────────────────────────────────────────────

def test_streams_active_and_status_aggregate_per_drone(client, valkey):
    valkey.xadd("drone:frames:DRN-010", {"frame": JPEG_A, "drone_id": "DRN-010"})   # pre-upgrade
    valkey.xadd("drone:frames:DRN-010", {"frame": JPEG_A, "drone_id": "DRN-010"})
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    for seq in (1, 2, 3):
        assert frame(client, "DRN-010", "MSN-1", seq).status_code == 202
    assert start(client, "DRN-010", "MSN-2").status_code == 201
    stop_drain(client, "MSN-1")
    for seq in (1, 2, 3, 4):
        assert frame(client, "DRN-010", "MSN-2", seq).status_code == 202
    consume_all(valkey, "mission:frames:MSN-2")                          # MSN-2 fully analysed

    body = client.get("/ingest/streams/active").json()
    assert body["count"] == 1
    d = body["drones"][0]
    assert d["drone_id"] == "DRN-010" and d["status"] == "live" and d["mission_status"] == "active"
    assert d["frame_count"] == 9                                         # 2 legacy + 3 + 4
    assert d["messages_ready"] == 5                                      # 2 + 3 + 0
    assert d["mission_id"] == "MSN-2" and d["stream_key"] == "mission:frames:MSN-2"
    by = {m["stream_key"]: m for m in d["missions"]}
    assert by["drone:frames:DRN-010"]["status"] == "legacy"
    assert by["mission:frames:MSN-1"] == dict(by["mission:frames:MSN-1"], mission_id="MSN-1",
                                              status="draining", frame_count=3, remaining=3)
    assert by["mission:frames:MSN-2"] == dict(by["mission:frames:MSN-2"], mission_id="MSN-2",
                                              status="live", frame_count=4, remaining=0)

    st = {x["drone_id"]: x for x in client.get("/ingest/status").json()["drones"]}
    assert st["DRN-010"]["queue"]["messages_ready"] == 5
    assert st["DRN-010"]["queue"]["queue_name"] == "mission:frames:MSN-2"
    one = client.get("/ingest/status/DRN-010").json()
    assert one["queue"]["messages_ready"] == 5 and one["mission_id"] == "MSN-2"


def test_latest_frame_follows_the_active_mission(client, valkey):
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    assert frame(client, "DRN-010", "MSN-1", jpeg=JPEG_A).status_code == 202
    r = client.get("/ingest/frame/DRN-010/latest")
    assert r.status_code == 200 and r.content == JPEG_A and r.headers["X-Mission-ID"] == "MSN-1"
    assert start(client, "DRN-010", "MSN-2").status_code == 201
    stop_drain(client, "MSN-1")
    r = client.get("/ingest/frame/DRN-010/latest")                      # MSN-2 has no frame yet
    assert r.status_code == 200 and r.content == JPEG_A
    assert frame(client, "DRN-010", "MSN-2", jpeg=JPEG_B).status_code == 202
    assert frame(client, "DRN-010", "MSN-1", seq=9, jpeg=JPEG_A).status_code == 202   # late MSN-1
    r = client.get("/ingest/frame/DRN-010/latest")
    assert r.status_code == 200 and r.content == JPEG_B
    assert r.headers["X-Mission-ID"] == "MSN-2" and r.headers["X-Stream-Status"] == "live"
    assert client.get("/ingest/frame/DRN-NONE/latest").status_code == 404


def test_purge_by_drone_removes_every_stream_and_the_registry(client, valkey):
    valkey.xadd("drone:frames:DRN-010", {"frame": JPEG_A, "drone_id": "DRN-010"})
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    assert frame(client, "DRN-010", "MSN-1").status_code == 202
    assert start(client, "DRN-010", "MSN-2").status_code == 201
    assert frame(client, "DRN-010", "MSN-2").status_code == 202
    assert start(client, "DRN-011", "MSN-3", sub="op-b").status_code == 201
    assert frame(client, "DRN-011", "MSN-3", sub="op-b").status_code == 202

    r = client.post("/ingest/streams/DRN-010/purge", headers=H(sub="op-a"))
    assert r.status_code == 403                                          # operators may not purge
    r = client.post("/ingest/streams/DRN-010/purge", headers=H(sub="officer", roles="control-room"))
    assert r.status_code == 200, r.text
    assert sorted(r.json()["missions"]) == ["MSN-1", "MSN-2"]

    assert valkey.exists("mission:frames:MSN-1", "mission:frames:MSN-2", "drone:frames:DRN-010") == 0
    assert valkey.exists("drone:missions:DRN-010", "drone:active_mission:DRN-010") == 0
    for sid in ("MSN-1", "MSN-2"):
        i = idx(valkey, sid)
        assert i["status"] == "ended" and i.get("purged_at")
        assert 0 < valkey.ttl(f"mission:index:{sid}") <= 7 * 24 * 3600
    assert valkey.xlen("mission:frames:MSN-3") == 1                      # other drone untouched
    assert s(valkey.get("drone:active_mission:DRN-011")) == "MSN-3"

    r = frame(client, "DRN-010", "MSN-2")                                # edge still sending
    assert r.status_code == 409 and err(r) == "mission_ended"
    assert valkey.exists("mission:frames:MSN-2") == 0
    assert [d["drone_id"] for d in client.get("/ingest/streams/active").json()["drones"]] == ["DRN-011"]

    r = client.delete("/ingest/streams/DRN-011", headers=H(sub="svc", roles="", user="service-account-x"))
    assert r.status_code == 200 and r.json()["missions"] == ["MSN-3"]
    assert valkey.keys("mission:frames:*") == []


def test_legacy_drone_stream_is_listed_read_and_drained(client, valkey):
    for _ in range(3):
        valkey.xadd("drone:frames:DRN-OLD", {"frame": JPEG_B, "drone_id": "DRN-OLD"})

    d = client.get("/ingest/streams/active").json()["drones"]
    assert len(d) == 1 and d[0]["drone_id"] == "DRN-OLD"
    assert d[0]["frame_count"] == 3 and d[0]["messages_ready"] == 3 and d[0]["status"] == "draining"
    assert d[0]["stream_key"] == "drone:frames:DRN-OLD" and d[0]["missions"][0]["status"] == "legacy"
    assert client.get("/ingest/frame/DRN-OLD/latest").content == JPEG_B
    st = {x["drone_id"]: x for x in client.get("/ingest/status").json()["drones"]}
    assert st["DRN-OLD"]["queue"]["messages_ready"] == 3

    client.portal.call(main._cleanup_orphan_streams)
    assert valkey.exists("drone:frames:DRN-OLD") == 1                     # unread: kept
    consume_all(valkey, "drone:frames:DRN-OLD")
    client.portal.call(main._cleanup_orphan_streams)
    assert valkey.exists("drone:frames:DRN-OLD") == 0


def test_legacy_stream_drained_while_the_drone_flies_a_mission(client, valkey):
    valkey.xadd("drone:frames:DRN-010", {"frame": JPEG_A, "drone_id": "DRN-010"})
    assert start(client, "DRN-010", "MSN-1").status_code == 201
    assert frame(client, "DRN-010", "MSN-1").status_code == 202
    consume_all(valkey, "drone:frames:DRN-010")
    client.portal.call(main._cleanup_orphan_streams)
    assert valkey.exists("drone:frames:DRN-010") == 0
    assert valkey.xlen("mission:frames:MSN-1") == 1
    assert "DRN-010" in main._active_missions                             # drone state kept
    ev = [e for e in client.events if e.get("event") == "mission_drained"][-1]
    assert ev["mission_id"] is None and ev["drone_still_active"] is True


# ── STREAM_KEY_MODE=drone: today's behaviour ──────────────────────────────────

def test_drone_mode_is_unchanged(drone_client, valkey):
    c = drone_client
    assert valkey.exists("drone:frames:DRONE_KL_001") == 1               # pre-declared as before
    assert [s(g["name"]) for g in valkey.xinfo_groups("drone:frames:DRONE_KL_001")] == ["analytics"]

    r = frame(c, "DRN-010", "MSN-NEVER-STARTED", sub="anyone")           # no mission, any token
    assert r.status_code == 202 and r.json()["queue_name"] == "drone:frames:DRN-010"
    assert start(c, "DRN-010", "MSN-1").status_code == 201
    assert start(c, "DRN-010", "MSN-2", sub="op-b").status_code == 201  # no ownership checks
    assert frame(c, "DRN-010", "MSN-1", sub="op-c").status_code == 202
    assert valkey.xlen("drone:frames:DRN-010") == 2
    assert b"mission_id" not in valkey.xrange("drone:frames:DRN-010", count=1)[0][1]
    assert valkey.keys("mission:frames:*") == [] and valkey.keys("mission:index:*") == []
    assert valkey.keys("drone:missions:*") == [] and valkey.keys("drone:active_mission:*") == []

    d = {x["drone_id"]: x for x in c.get("/ingest/streams/active").json()["drones"]}
    assert d["DRN-010"]["frame_count"] == 2 and "missions" not in d["DRN-010"]
    assert c.get("/ingest/frame/DRN-010/latest").status_code == 200
    assert end(c, "DRN-010", "MSN-9", sub="op-z").status_code == 200
    r = c.post("/ingest/streams/DRN-010/purge", headers=H(sub="o", roles="supervisor"))
    assert r.status_code == 200 and r.json() == {"status": "purged", "drone_id": "DRN-010"}
    assert valkey.exists("drone:frames:DRN-010") == 0
