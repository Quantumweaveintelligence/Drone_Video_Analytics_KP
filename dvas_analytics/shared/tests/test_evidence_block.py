"""shared/evidence.py and the hashed upload (evidence plan E1/E3). No MinIO/DB needed.

Run from dvas_analytics/:  python -m pytest shared/tests -q
"""
import asyncio
import hashlib
import json

from shared import evidence
from shared.evidence import EventGate, ThresholdGate, bbox_xyxy, evidence_block, frame_context, model_info


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _entry(**meta):
    return {
        "jpeg_b64": "x",
        "metadata": json.dumps({"mission_id": "MSN-1", "latitude": 8.52, "longitude": 76.93,
                                "altitude_m": 60.0, "heading_deg": 140, **meta}),
        "frame_sequence": "18231",
        "capture_ts": "1793600103.12",
        "ts": "1793600103480",
        "timestamp": "2026-11-02T10:15:03Z",
    }


def test_frame_context_reads_ingest_fields():
    ctx = frame_context(_entry())
    assert ctx["mission_id"] == "MSN-1"
    assert ctx["frame"] == {"frame_sequence": 18231, "capture_ts": 1793600103.12,
                            "ingest_ts": 1793600103.48, "timestamp": "2026-11-02T10:15:03Z"}
    assert ctx["geo"] == {"lat": 8.52, "lon": 76.93, "alt_m": 60.0, "heading_deg": 140.0}


def test_frame_context_without_gps_or_metadata():
    ctx = frame_context({"jpeg_b64": "x", "metadata": json.dumps({"latitude": 0.0, "longitude": 0.0})})
    assert ctx["geo"] is None and ctx["mission_id"] is None
    assert frame_context({})["frame"]["frame_sequence"] is None
    assert frame_context({"metadata": b"not json"})["mission_id"] is None


def test_evidence_block_roles_and_dropped_uploads():
    a = {"bucket": "b", "key": "k1", "content_type": "image/jpeg", "sha256": "aa", "bytes": 3, "kind": "source_frame"}
    c = {"bucket": "b", "key": "k2", "content_type": "image/jpeg", "sha256": "cc", "bytes": 4, "kind": "plate_crop"}
    block = evidence_block([a, None, c], frame_context(_entry()), detection={"track_id": 7})
    assert block["schema"] == 1
    assert [(o["key"], o["role"]) for o in block["objects"]] == [("k1", "primary"), ("k2", "secondary")]
    assert block["geo"]["lat"] == 8.52 and block["detection"] == {"track_id": 7}
    json.dumps(block)    # must be JSON-serialisable as event attributes


def test_evidence_block_geo_override():
    block = evidence_block([], frame_context(_entry()), geo={"lat": 1.0, "lon": 2.0})
    assert block["geo"] == {"lat": 1.0, "lon": 2.0} and block["objects"] == []


def test_model_info_hashes_weights_once(tmp_path, monkeypatch):
    w = tmp_path / "w.pt"
    w.write_bytes(b"weights")
    monkeypatch.setenv("DVAS_GIT_SHA", "abc123")
    info = model_info("crowd", str(w))
    assert info == {"service": "crowd", "weights": "w.pt",
                    "weights_sha256": hashlib.sha256(b"weights").hexdigest(), "git_sha": "abc123"}
    assert model_info("crowd", str(tmp_path / "missing.pt"))["weights_sha256"] is None


def test_bbox_normalisation():
    assert bbox_xyxy({"x1": 1, "y1": 2, "x2": 3, "y2": 4}) == [1.0, 2.0, 3.0, 4.0]
    assert bbox_xyxy([1, 2, 3, 4]) == [1.0, 2.0, 3.0, 4.0]
    assert bbox_xyxy(None) is None and bbox_xyxy([1, 2]) is None


def test_event_gate_cooldown_per_key():
    clock = Clock()
    gate = EventGate(60, clock=clock)
    assert gate.allow(("KL01", "M1"))
    assert not gate.allow(("KL01", "M1"))
    assert gate.allow(("KL01", "M2"))
    clock.t += 60
    assert gate.allow(("KL01", "M1"))


def test_threshold_gate_one_event_per_surge():
    clock = Clock()
    gate = ThresholdGate(cooldown_s=60, rearm_ratio=0.8, clock=clock)
    fired = [gate.update("D1", c, 100) for c in (90, 120, 150, 130, 110)]
    assert fired == [False, True, False, False, False]
    clock.t += 10
    assert not gate.update("D1", 70, 100)      # dropped, but cooldown not over → stays disarmed
    assert not gate.update("D1", 120, 100)
    clock.t += 60
    assert not gate.update("D1", 70, 100)      # re-armed now
    assert gate.update("D1", 105, 100)
    assert gate.update("D2", 200, 100)         # drones independent


def test_upload_object_hashes_and_tags(monkeypatch):
    from shared import minio_client

    calls = []

    class FakeMinio:
        async def bucket_exists(self, b):
            return True

        async def put_object(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(minio_client, "get_client", lambda: FakeMinio())
    monkeypatch.setattr(minio_client, "_buckets_ensured", set())
    res = asyncio.run(minio_client.upload_object("dvas-x", "k.jpg", b"abc", "image/jpeg", kind="plate_crop"))
    digest = hashlib.sha256(b"abc").hexdigest()
    assert res == {"bucket": "dvas-x", "key": "k.jpg", "content_type": "image/jpeg",
                   "sha256": digest, "bytes": 3, "kind": "plate_crop"}
    assert calls[0]["metadata"] == {"sha256": digest}
    assert asyncio.run(minio_client.upload_bytes("dvas-x", "k2.jpg", b"abc")) == "k2.jpg"


def test_upload_object_failure_returns_none(monkeypatch):
    from shared import minio_client

    class Broken:
        async def bucket_exists(self, b):
            return True

        async def put_object(self, **kw):
            raise ConnectionError("down")

    monkeypatch.setattr(minio_client, "get_client", lambda: Broken())
    assert asyncio.run(minio_client.upload_object("dvas-x", "k.jpg", b"abc")) is None
    assert asyncio.run(minio_client.upload_bytes("dvas-x", "k.jpg", b"abc")) is None
