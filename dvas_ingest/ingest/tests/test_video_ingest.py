"""v2 R1 — video uplink (/ingest/video/ws), recording, and the decoder that feeds the analytics
without losing frames when they fall behind. Against a real Valkey, like test_mission_streams:

    docker run --rm -d --name dvas-valkey-2a -p 127.0.0.1:6391:6379 valkey/valkey:7.2
    cd dvas_ingest/ingest && python -m pytest tests/test_video_ingest.py -q

Needs ffmpeg (libx264) on PATH and PyAV; skipped otherwise.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "deploy", "runpod", "tools"))
av = pytest.importorskip("av")
cv2 = pytest.importorskip("cv2")

import main  # noqa: E402
import queue_manager as qm  # noqa: E402
import video_decoder as vd  # noqa: E402
import video_store as vs  # noqa: E402
from send_ts import ts_chunks  # noqa: E402
from test_mission_streams import H, VALKEY_URL, _make_client, _reset_main_state, _valkey, s, start, end  # noqa: E402

DRONE, MISSION = "DRN-900", "MSN-VIDEO-01"
SID = qm.mission_sid(MISSION)
KEY = qm.mission_stream_key(SID)


@pytest.fixture(autouse=True)
def _decode_without_services(monkeypatch):
    # These tests run the decoder with no analytics service publishing stats; R6.2's
    # "decode only while a service runs" has its own test below.
    monkeypatch.setattr(vd, "DECODE_ONLY_WITH_SERVICES", False)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("clips")
    out = {}
    for name, extra, secs in (("plain", ["-bf", "0"], 4), ("bframes", ["-bf", "2"], 3)):
        p = d / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=30", "-t", str(secs), "-c:v", "libx264",
                        "-g", "15", *extra, "-pix_fmt", "yuv420p", str(p)], check=True)
        out[name] = str(p)
    return out


@pytest.fixture
def valkey():
    r = _valkey()
    if r is None:
        pytest.skip(f"Valkey not reachable at {VALKEY_URL}")
    r.flushdb()
    yield r
    r.flushdb()


@pytest.fixture
def client(monkeypatch, valkey, tmp_path):
    monkeypatch.setattr(vs, "RECORD_ROOT", tmp_path / "rec")
    monkeypatch.setattr(vs, "MIN_FREE_PCT", 0.0)
    monkeypatch.setattr(vs, "MIN_FREE_GB", 0.0)
    monkeypatch.setattr(vs, "SEGMENT_S", 1.0)
    monkeypatch.setattr(main.video_relay, "ENABLED", False)

    async def token(t):
        sub = t.split(":", 1)[1] if t.startswith("sub:") else "op-a"
        return {"sub": sub, "preferred_username": sub, "realm_access": {"roles": ["drone-operator"]}}
    monkeypatch.setattr(main, "validate_token_string", token)
    tc = _make_client(monkeypatch, valkey, "mission")
    try:
        with tc:
            yield tc
    finally:
        main.app.dependency_overrides.clear()
        _reset_main_state()
        main._qm = None


def hello(drone=DRONE, mission=MISSION, **kw):
    return json.dumps({"type": "hello", "v": 1, "drone_id": drone, "mission_id": mission, "codec": "h264",
                       "container": "mpegts", "width": 320, "height": 240, "fps": 30, "source": "file",
                       "next_seq": 1, **kw})


def ws_url(drone=DRONE, mission=MISSION):
    return f"/ingest/video/ws?drone_id={drone}&mission_id={mission}"


def send_clip(ws, path, first_seq=1, bye=True, pts_offset_s=0.0):
    last = first_seq - 1
    for meta, data, _t in ts_chunks(path, first_seq=first_seq, capture_t0=1762671000.0,
                                    pts_offset_s=pts_offset_s):
        if meta is not None:
            ws.send_text(json.dumps(meta))
            last = meta["seq"]
        if data:
            ws.send_bytes(data)
    if bye:
        ws.send_text(json.dumps({"type": "bye", "last_seq": last}))
    else:                                     # connection drops after the server got everything
        t_end = time.monotonic() + 15
        while time.monotonic() < t_end:
            msg = json.loads(ws.receive_text())
            if msg.get("type") == "stats" and msg.get("last_seq") == last:
                break
    return last


def stream_clip(client, path, first_seq=1, bye=True, token="op-a"):
    with client.websocket_connect(ws_url(), headers={"authorization": f"Bearer sub:{token}"}) as ws:
        ws.send_text(hello(next_seq=first_seq))
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready", ready
        last = send_clip(ws, path, first_seq, bye)
        if bye:
            msg = ws.receive()
            while msg.get("type") != "websocket.close":
                msg = ws.receive()
    return ready, last


def refused(client, headers=None, hello_doc=None, drone=DRONE, mission=MISSION):
    with client.websocket_connect(ws_url(drone, mission), headers=headers or {}) as ws:
        if hello_doc is not None:
            ws.send_text(hello_doc)
        err = json.loads(ws.receive_text())
        close = ws.receive()
    return err, close.get("code")


def vhash(valkey):
    return {s(k): s(v) for k, v in valkey.hgetall(vs.video_key(SID)).items()}


def run_decoder(stop_after=None, consume=None, timeout=60.0):
    """Run the decoder service until every mission is decoded (or stop_after frames)."""
    async def go():
        q = qm.QueueManager()
        await q.connect()
        svc = vd.DecoderService(q)
        loop = asyncio.get_running_loop()
        await svc.scan(loop)
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            if consume:
                await asyncio.to_thread(consume)
            alive = [t for t in svc.running.values() if t.is_alive()]
            if stop_after is not None:
                for t in alive:
                    if t.published >= stop_after:
                        t.stop.set()
            if not alive:
                break
            await asyncio.sleep(0.05)
        for t in svc.running.values():
            t.stop.set()
            t.join(5)
        await q.close()
        return svc
    return asyncio.run(go())


def entries(valkey):
    out = []
    for eid, f in valkey.xrange(KEY):
        out.append({s(k): (v if s(k) == "frame" else s(v)) for k, v in f.items()})
    return out


def consumer(valkey, group="person-group", read_only=False):
    """An analyzer group: reads everything; acks unless read_only. Returns the seqs read."""
    seen = []
    valkey.set(f"dvas:svc_stats:{group[:-6]}", "{}", ex=30)

    def step():
        if not valkey.exists(KEY):
            return
        try:
            valkey.xgroup_create(KEY, group, id="0")
        except Exception:
            pass
        got = valkey.xreadgroup(group, "c1", {KEY: ">"}, count=1000)
        for _k, es in got or []:
            for eid, f in es:
                seen.append(int(s(f[b"frame_seq"])))
                if not read_only:
                    valkey.xack(KEY, group, eid)
        valkey.set(f"dvas:svc_stats:{group[:-6]}", "{}", ex=30)
    return seen, step


# ── handshake ────────────────────────────────────────────────────────────────

def test_refusals(client, valkey):
    err, code = refused(client)                          # no token
    assert code == 4401 and err["code"] == "unauthorized"
    auth = {"authorization": "Bearer sub:op-a"}
    err, code = refused(client, auth, hello())           # mission not started
    assert code == 4409 and err["code"] == "mission_not_started"
    assert start(client, DRONE, MISSION).status_code < 300
    err, code = refused(client, {"authorization": "Bearer sub:op-b"}, hello())
    assert code == 4403 and err["code"] == "not_mission_operator"
    err, code = refused(client, auth, hello(codec="hevc"))
    assert code == 4415
    err, code = refused(client, auth, hello(mission="OTHER"))
    assert code == 1008
    end(client, DRONE, MISSION)
    err, code = refused(client, auth, hello())           # draining: no new video
    assert code == 4409


# ── recording ────────────────────────────────────────────────────────────────

def test_recording_segments_manifest_and_state(client, valkey, clips):
    assert start(client, DRONE, MISSION).status_code < 300
    ready, last = stream_clip(client, clips["plain"])
    assert ready["last_seq"] == 0 and ready["analytics_fps"] == 20
    h = vhash(valkey)
    assert h["status"] == "ended" and int(h["last_seq_rx"]) == last == 120
    segs = vs.segments(SID)
    assert len(segs) >= 3                                   # 4 s at SEGMENT_S=1, rotated at keyframes
    ok, n, why = vs.verify_manifest(SID)
    assert ok and n == len(segs), why
    for idx in segs:                                        # every segment starts with a keyframe
        with av.open(str(vs.seg_path(SID, idx))) as c:
            first = next(c.decode(c.streams.video[0]))
            assert first.key_frame
    # telemetry reached the drone's live state
    assert main._latest_metadata[DRONE]["frame_sequence"] > 100
    assert main._latest_metadata[DRONE]["transport"] == "h264_ws"
    # tampering is detected
    with open(vs.seg_path(SID, segs[1]), "r+b") as f:
        f.seek(400)
        f.write(b"\x00")
    assert vs.verify_manifest(SID)[0] is False


# ── decoder ──────────────────────────────────────────────────────────────────

def _check_frames(es, src_frames, fps_src=30):
    seqs = [int(e["frame_seq"]) for e in es]
    assert seqs == list(range(1, len(seqs) + 1))            # contiguous: no false "lost" frames
    expect = round(src_frames * 20 / fps_src)
    assert abs(len(seqs) - expect) <= 2, (len(seqs), expect)
    pts = [json.loads(e["metadata"])["pts_ms"] for e in es]
    assert pts == sorted(pts)                               # presentation order (B-frames too)
    for e in es:
        meta = json.loads(e["metadata"])
        assert abs(meta["latitude"] - (8.5241 + meta["edge_seq"] * 1e-6)) < 1e-9   # its own telemetry
        assert e["source"] == "h264" and int(e["rx_ts_ms"]) > 0
        assert float(e["capture_ts"]) >= 1762671000.0
        img = cv2.imdecode(__import__("numpy").frombuffer(e["frame"], "uint8"), cv2.IMREAD_COLOR)
        assert img.shape == (240, 320, 3)                   # source resolution
    return seqs


@pytest.mark.parametrize("clip,n", [("plain", 120), ("bframes", 90)])
def test_decoder_feeds_sampled_frames_with_their_telemetry(client, valkey, clips, clip, n):
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips[clip])
    run_decoder()
    _check_frames(entries(valkey), n)
    h = vhash(valkey)
    assert h["decoder_done"] == "1" and h["last_entry_id"]


@pytest.mark.parametrize("offset", [True, False])
def test_a_looped_clip_is_analysed_on_every_pass(client, valkey, clips, offset):
    # send_ts --loop: the second pass continues the PTS (offset=True, as the contract wants);
    # a sender that restarts its PTS (offset=False) must not stop the analytics feed either
    assert start(client, DRONE, MISSION).status_code < 300
    with client.websocket_connect(ws_url(), headers={"authorization": "Bearer sub:op-a"}) as ws:
        ws.send_text(hello())
        assert json.loads(ws.receive_text())["type"] == "ready"
        last = send_clip(ws, clips["plain"], bye=False)
        send_clip(ws, clips["plain"], first_seq=last + 1, pts_offset_s=4.1 if offset else 0.0)
        msg = ws.receive()
        while msg.get("type") != "websocket.close":
            msg = ws.receive()
    run_decoder()
    frames = entries(valkey)
    edge = [int(json.loads(e["metadata"])["edge_seq"]) for e in frames]
    assert len(set(edge)) == len(edge)                           # no frame twice
    assert max(edge) > last                                      # the second pass was analysed
    assert len(frames) >= (158 if offset else 154)               # 2 × 80 sampled (± boundary frames)
    assert vhash(valkey)["decoder_done"] == "1"


def test_flow_control_waits_for_a_slow_service_and_loses_nothing(client, valkey, clips, monkeypatch):
    monkeypatch.setattr(vd, "WINDOW", 10)
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips["plain"])
    # a group that reads but does not acknowledge yet: the decoder must stop at the window
    valkey.xadd(KEY, {"frame": b"x", "frame_seq": "0"})     # stream exists → group can be created
    valkey.xtrim(KEY, maxlen=0)
    seen, read_only = consumer(valkey, read_only=True)
    read_only()                                             # the analyzer's consumer exists
    svc_thread = threading.Thread(target=run_decoder, kwargs={"timeout": 4.0})
    svc_thread.start()
    time.sleep(2.0)
    read_only()
    assert 8 <= len(entries(valkey)) <= 12                  # held at the window, nothing trimmed
    svc_thread.join()
    seen2, ack_all = consumer(valkey)
    valkey.xack(KEY, "person-group", *[e[0] for e in valkey.xrange(KEY)])
    run_decoder(consume=ack_all)
    got = sorted(set(seen + seen2) - {0})
    assert got == list(range(1, 81)) or abs(len(got) - 80) <= 2 and got == list(range(1, len(got) + 1))
    assert vhash(valkey)["decoder_done"] == "1"


def test_switched_off_service_does_not_hold_the_feed(client, valkey, clips, monkeypatch):
    monkeypatch.setattr(vd, "WINDOW", 10)
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips["plain"])
    valkey.xadd(KEY, {"frame": b"x", "frame_seq": "0"})
    valkey.xgroup_create(KEY, "crowd-group", id="$")
    valkey.xreadgroup("crowd-group", "c1", {KEY: ">"}, count=1)   # a consumer, but no stats key
    run_decoder(timeout=20)
    assert vhash(valkey)["decoder_done"] == "1"


def test_latest_frame_service_never_holds_the_feed(client, valkey, clips, monkeypatch):
    # crowd / vessel read the newest frame only (R6.1): a busy one must not stop the decoder
    monkeypatch.setattr(vd, "WINDOW", 10)
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips["plain"])
    valkey.xadd(KEY, {"frame": b"x", "frame_seq": "0"})
    valkey.xgroup_create(KEY, "crowd-group", id="$")
    valkey.xreadgroup("crowd-group", "c1", {KEY: ">"}, count=1)   # reads, never acknowledges
    valkey.set("dvas:svc_stats:crowd", json.dumps({"service": "crowd", "mode": "newest"}), ex=60)
    run_decoder(timeout=20)
    h = vhash(valkey)
    assert h["decoder_done"] == "1" and int(h["analytics_seq"]) >= 78


def test_nothing_is_decoded_while_no_service_runs(client, valkey, clips, monkeypatch):
    monkeypatch.setattr(vd, "DECODE_ONLY_WITH_SERVICES", True)     # R6.2, the production default
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips["plain"])
    run_decoder(timeout=20)
    h = vhash(valkey)
    assert h["decoder_done"] == "1"                                 # the mission still drains
    assert entries(valkey) == [] and int(h.get("analytics_seq") or 0) == 0


def test_restart_resumes_without_gap_or_duplicate(client, valkey, clips):
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips["plain"])
    run_decoder(stop_after=33)
    first = [int(e["frame_seq"]) for e in entries(valkey)]
    assert 30 <= len(first) < 80 and vhash(valkey).get("decoder_done") != "1"   # stopped part-way (80 in all)
    run_decoder()
    seqs = _check_frames(entries(valkey), 120)
    assert seqs[:len(first)] == first


def test_reconnect_continues_the_sequence(client, valkey, clips):
    assert start(client, DRONE, MISSION).status_code < 300
    _, last = stream_clip(client, clips["plain"], bye=False)
    ready, last2 = stream_clip(client, clips["bframes"], first_seq=last + 1)
    assert ready["last_seq"] == last == 120
    run_decoder()
    es = entries(valkey)
    seqs = [int(e["frame_seq"]) for e in es]
    assert seqs == list(range(1, len(seqs) + 1)) and abs(len(seqs) - 140) <= 3
    edge = [json.loads(e["metadata"])["edge_seq"] for e in es]
    assert max(edge) >= last2 - 3 and min(edge[-60:]) > last          # second connection's frames


def test_drain_waits_for_the_decoder(client, valkey, clips):
    assert start(client, DRONE, MISSION).status_code < 300
    stream_clip(client, clips["plain"])
    assert end(client, DRONE, MISSION).status_code < 300
    seen, ack_all = consumer(valkey)
    assert client.portal.call(main._try_finalize_mission, SID, 0.0, 0.0) is None   # not decoded yet
    run_decoder(consume=ack_all)
    ack_all()
    assert client.portal.call(main._try_finalize_mission, SID, 0.0, 0.0) == "finalized"
    assert len(seen) >= 78


def test_publish_maxlen_is_the_safety_bound(valkey, monkeypatch):
    monkeypatch.setattr(qm, "REDIS_URL", VALKEY_URL)
    async def go():
        q = qm.QueueManager()
        await q.connect()
        for i in range(400):
            await q.publish_frame("D", b"\xff\xd8\xff", {"frame_sequence": i, "rx_ts_ms": 1},
                                  stream_key="mission:frames:T", maxlen=20)
        await q.close()
    asyncio.run(go())
    assert valkey.xlen("mission:frames:T") <= 150         # approximate MAXLEN: whole nodes of 100
    f = valkey.xrange("mission:frames:T", count=1)[0][1]
    assert f[b"source"] == b"h264" and f[b"rx_ts_ms"] == b"1"


def test_live_relay_gets_a_slot_and_the_stream_from_a_keyframe(client, valkey, clips, tmp_path, monkeypatch):
    out = tmp_path / "relayed.ts"
    fake = tmp_path / "fake_ffmpeg.sh"
    fake.write_text(f"#!/bin/sh\ncat > '{out}'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(main.video_relay, "ENABLED", True)
    monkeypatch.setattr(main.video_relay, "FFMPEG", str(fake))
    assert start(client, DRONE, MISSION).status_code < 300
    seen_slot = []

    with client.websocket_connect(ws_url(), headers={"authorization": "Bearer sub:op-a"}) as ws:
        ws.send_text(hello())
        assert json.loads(ws.receive_text())["type"] == "ready"
        seen_slot.append(s(valkey.get("live:slot:live01")))
        assert vhash(valkey)["live_slot"] == "live01"
        info = client.get(f"/ingest/video/live/{DRONE}", headers=H()).json()
        assert info["src"] == "live01" and info["width"] == 320
        assert client.get("/ingest/video/slot/live01", headers=H()).json()["drone_id"] == DRONE
        # the dashboard backend calls these with a role-less service token: no JWT needed
        assert client.get(f"/ingest/video/live/{DRONE}").json()["src"] == "live01"
        assert client.get("/ingest/video/slot/live01").json()["drone_id"] == DRONE
        send_clip(ws, clips["plain"])
        msg = ws.receive()
        while msg.get("type") != "websocket.close":
            msg = ws.receive()
    assert seen_slot == [SID]
    assert valkey.get("live:slot:live01") is None and "live_slot" not in vhash(valkey)   # freed
    assert client.get(f"/ingest/video/live/{DRONE}", headers=H()).status_code == 404
    assert client.get("/ingest/video/slot/live01", headers=H()).status_code == 404
    assert valkey.ttl("live:cooldown:live01") > 60           # not handed to another mission yet
    time.sleep(0.2)
    relayed = out.read_bytes()
    recorded = b"".join(vs.seg_path(SID, i).read_bytes() for i in vs.segments(SID))
    assert relayed and recorded.endswith(relayed)          # everything from the first keyframe
    with av.open(str(out)) as c:
        assert sum(1 for _ in c.decode(c.streams.video[0])) == 120


def test_grid_substream_runs_only_while_asked_for(client, valkey, clips, tmp_path, monkeypatch):
    # fake ffmpeg: writes its stdin to a file named after the go2rtc stream it pushes to
    fake = tmp_path / "fake_ffmpeg.sh"
    fake.write_text(f"#!/bin/sh\nfor a in \"$@\"; do last=\"$a\"; done\ncat > '{tmp_path}'/\"$(basename \"$last\")\"\n")
    fake.chmod(0o755)
    monkeypatch.setattr(main.video_relay, "ENABLED", True)
    monkeypatch.setattr(main.video_relay, "SUBSTREAM", True)
    monkeypatch.setattr(main.video_relay, "FFMPEG", str(fake))
    monkeypatch.setattr(main.video_relay, "_sub_encoder", "libx264")
    assert start(client, DRONE, MISSION).status_code < 300

    with client.websocket_connect(ws_url(), headers={"authorization": "Bearer sub:op-a"}) as ws:
        ws.send_text(hello())
        assert json.loads(ws.receive_text())["type"] == "ready"
        plain = client.get(f"/ingest/video/live/{DRONE}").json()
        assert plain["src"] == "live01" and plain["sub"] is False
        assert valkey.get("live:sub_want:live01") is None            # nobody asked: no transcode
        info = client.get(f"/ingest/video/live/{DRONE}?sub=1").json()
        assert info["src"] == "live01_sub" and info["main_src"] == "live01" and info["sub"] is True
        assert 0 < valkey.ttl("live:sub_want:live01") <= main.LIVE_SUB_WANT_S
        assert client.get("/ingest/video/slot/live01_sub").json()["drone_id"] == DRONE   # authorize path
        send_clip(ws, clips["plain"])
        msg = ws.receive()
        while msg.get("type") != "websocket.close":
            msg = ws.receive()
    time.sleep(0.2)
    main_bytes = (tmp_path / "live01").read_bytes()
    sub_bytes = (tmp_path / "live01_sub").read_bytes()
    assert main_bytes[:1] == b"\x47" and sub_bytes[:1] == b"\x47"   # both fed from a keyframe
    assert len(sub_bytes) == len(main_bytes)                           # same input; ffmpeg re-encodes
    assert client.get("/ingest/video/slot/live09_sub").status_code == 404


def test_full_volume_refuses_new_video(client, valkey, tmp_path, monkeypatch):
    guard = tmp_path / "disk_guard.json"
    guard.write_text(json.dumps({"pct": 93, "used_kb": 1, "volume_gb": "100", "at": time.time()}))
    monkeypatch.setattr(vs, "DISK_GUARD_FILE", guard)
    assert start(client, DRONE, MISSION).status_code < 300
    err, code = refused(client, {"authorization": "Bearer sub:op-a"}, hello())
    assert code == 1013 and err["code"] == "disk_full"
    guard.write_text(json.dumps({"pct": 40, "used_kb": 1, "volume_gb": "100", "at": time.time()}))
    with client.websocket_connect(ws_url(), headers={"authorization": "Bearer sub:op-a"}) as ws:
        ws.send_text(hello())
        assert json.loads(ws.receive_text())["type"] == "ready"


def test_mission_end_after_bye_keeps_the_video_ended():
    """mission/end right after `bye` (while the recording is still being sealed) closes the
    connection again; the video must stay `ended`, not turn `paused`."""
    class WS:
        async def close(self, code=1000, reason=""):
            pass
    conn = main._VideoConn(WS(), "sid", DRONE, MISSION)
    conn.close_code = -1
    asyncio.run(conn.close(1000, "mission ended"))
    assert conn.close_code == -1
    other = main._VideoConn(WS(), "sid2", DRONE, MISSION)
    asyncio.run(other.close(1000, "mission ended"))
    assert other.close_code == 1000


def test_a_server_close_ends_a_receive_that_would_wait_forever():
    """mission/end closes the connection while the handler waits in receive() and no
    disconnect ever arrives: the wait ends, so the slot and the stream limit are freed."""
    class WS:
        async def receive(self):
            await asyncio.sleep(3600)

        async def close(self, code=1000, reason=""):
            pass

    async def t():
        conn = main._VideoConn(WS(), "sid", DRONE, MISSION)
        task = asyncio.ensure_future(main._video_receive(conn, {}, None, None, "vkey"))
        await asyncio.sleep(0.05)
        await conn.close(1000, "mission ended")
        await asyncio.wait_for(task, 2.0)
        assert conn.close_code == 1000

    asyncio.run(t())


def test_a_silent_connection_is_closed_after_the_idle_timeout(monkeypatch):
    class WS:
        async def receive(self):
            await asyncio.sleep(3600)

    monkeypatch.setattr(main, "VIDEO_IDLE_TIMEOUT_S", 0.1)
    conn = main._VideoConn(WS(), "sid", DRONE, MISSION)
    with pytest.raises(main._VideoClose) as exc:
        asyncio.run(main._video_receive(conn, {}, None, None, "vkey"))
    assert exc.value.error == "idle"


def test_every_live_slot_is_declared_in_go2rtc():
    """v2 R9: go2rtc accepts pushes only to declared streams; ingest must not hand out a slot
    (or its grid sub-stream twin) that go2rtc.yaml lacks. 32 slots for up to 20 live feeds."""
    import re
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "deploy", "runpod", "etc", "go2rtc.yaml")
    if not os.path.exists(path):
        pytest.skip("deploy/ not next to the ingest sources")
    declared = set(re.findall(r"^  (live\d{2}(?:_sub)?):\s*$", open(path).read(), re.M))
    assert len(main.LIVE_SLOTS) == 32
    for name in main.LIVE_SLOTS:
        assert name in declared and f"{name}_sub" in declared, name


def test_open_video_uplink_marks_the_drone_live(monkeypatch):
    """v2 R9: with no analytics service selected nothing reaches the analytics stream, so the
    dashboard needs the uplink itself to show the drone live."""
    class Conn:
        def __init__(self, drone_id, stopping=False):
            self.drone_id, self.stopping = drone_id, stopping
    monkeypatch.setattr(main, "_video_conns", {"s1": Conn("DRN-1"), "s2": Conn("DRN-2", stopping=True)})
    assert main._video_uplink_open("DRN-1") is True
    assert main._video_uplink_open("DRN-2") is False          # closing
    assert main._video_uplink_open("DRN-3") is False


def test_relay_counts_and_reports_skips_and_exits(caplog):
    """v2 R9: the live relay's restarts / keyframe skips are logged and published per mission."""
    import logging
    import video_relay
    lr = video_relay.LiveRelay("live05")
    with caplog.at_level(logging.WARNING, logger="video_relay"):
        lr.main._note_skip(4096)
        lr.main._note_skip(4096)                          # within 10 s: counted, not logged again
    assert lr.stats() == {"relay_restarts": 0, "relay_skips": 2, "relay_dropped_kb": 8}
    assert sum("skipped to the next keyframe" in r.message for r in caplog.records) == 1
    lr.sub = video_relay.Relay("live05_sub", transcode=True)
    lr.sub._stderr.append("Error writing trailer")
    with caplog.at_level(logging.WARNING, logger="video_relay"):
        lr.sub._note_exit()
    assert "Error writing trailer" in caplog.records[-1].message
    assert lr.stats()["sub_skips"] == 0 and "sub_restarts" in lr.stats()
