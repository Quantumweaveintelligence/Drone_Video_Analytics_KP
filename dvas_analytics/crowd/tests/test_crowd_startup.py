"""Crowd service startup and /health (B27). torch/cv2/DB/Valkey/MinIO are stubbed.

Run from dvas_analytics/:  python -m pytest crowd/tests -q
"""
import asyncio
import importlib
import sys
import types

import pytest


class FakeCrowdAnalyzer:
    instances = []
    fail_load = False

    def __init__(self, model_path, device=None, alert_threshold=150):
        self.alert_threshold = alert_threshold
        self.ready = False
        self._model_path = None
        self.device = "cpu"
        FakeCrowdAnalyzer.instances.append(self)

    def switch_model(self, path):
        if FakeCrowdAnalyzer.fail_load:
            raise RuntimeError("bad weights")
        self._model_path = path
        self.ready = True


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)

    async def _noop(*a, **k):
        return None
    mod.__getattr__ = lambda attr: _noop   # any other import resolves to an async no-op
    return mod


@pytest.fixture
def crowd_main(monkeypatch):
    FakeCrowdAnalyzer.instances.clear()
    FakeCrowdAnalyzer.fail_load = False
    for name, mod in {
        "cv2": types.ModuleType("cv2"),
        "shared.db": _stub("shared.db"),
        "shared.redis_client": _stub("shared.redis_client"),
        "shared.minio_client": _stub("shared.minio_client"),
        "crowd.analyzer": _stub("crowd.analyzer", CrowdAnalyzer=FakeCrowdAnalyzer),
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "crowd.main", raising=False)
    main = importlib.import_module("crowd.main")

    async def _idle_loop():
        await asyncio.sleep(3600)
    monkeypatch.setattr(main, "_stream_loop", _idle_loop)
    yield main
    sys.modules.pop("crowd.main", None)


def _run_startup(main):
    async def go():
        await main.startup()
        health = await main.health()
        main._stream_task.cancel()
        return health
    return asyncio.run(go())


def test_startup_creates_analyzer_and_starts_stream(crowd_main):
    health = _run_startup(crowd_main)
    assert len(FakeCrowdAnalyzer.instances) == 1
    assert crowd_main.analyzer.ready
    assert crowd_main.analyzer.alert_threshold == crowd_main.cfg.CROWD_ALERT_THRESHOLD
    assert crowd_main._stream_running
    assert health["status"] == "ok" and health["model_loaded"] is True


def test_health_is_503_when_model_failed_to_load(crowd_main):
    FakeCrowdAnalyzer.fail_load = True
    resp = _run_startup(crowd_main)          # startup must not raise
    assert resp.status_code == 503
    assert b'"model_loaded":false' in resp.body


def test_health_is_503_before_startup(crowd_main):
    resp = asyncio.run(crowd_main.health())
    assert resp.status_code == 503


class _Upload:
    content_type = "image/jpeg"

    def __init__(self, data):
        self._data = data

    async def read(self):
        return self._data


def test_http_frame_one_event_per_surge_with_exact_upload(crowd_main, monkeypatch):
    import types as _types
    events, uploads = [], []

    async def write_confirmed_event(**kw):
        events.append(kw)
        return f"ev{len(events)}"

    async def upload_object(bucket, key, data, content_type="", kind=""):
        uploads.append((bucket, data, kind))
        return {"bucket": bucket, "key": key, "content_type": content_type, "sha256": "h", "bytes": len(data),
                "kind": kind}

    counts = iter([120, 130, 40])
    _run_startup(crowd_main)
    crowd_main.analyzer.alert_threshold = 100
    crowd_main.analyzer.analyze_frame = lambda frame, **kw: {
        "estimated_count": next(counts), "density_label": "high", "alert_triggered": True}
    monkeypatch.setattr(crowd_main, "write_confirmed_event", write_confirmed_event)
    monkeypatch.setattr(crowd_main.minio, "upload_object", upload_object, raising=False)
    monkeypatch.setattr(crowd_main, "cv2", _types.SimpleNamespace(imdecode=lambda *a: "frame", IMREAD_COLOR=1))

    for _ in range(3):
        asyncio.run(crowd_main.analyze_frame(file=_Upload(b"jpeg-bytes"), drone_id="D1", include_heatmap=False))
    assert len(events) == 1                                   # 120 fires, 130 same surge, 40 below
    block = events[0]["attributes"]["evidence"]
    assert block["objects"][0]["kind"] == "source_frame" and uploads[0][1] == b"jpeg-bytes"
    assert block["frame"]["source"] == "http_upload" and block["geo"] is None
