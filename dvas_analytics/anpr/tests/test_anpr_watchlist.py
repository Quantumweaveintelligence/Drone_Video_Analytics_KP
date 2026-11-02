"""ANPR watchlist survives restarts (B30). torch/cv2/DB/Valkey/MinIO are stubbed.

Run from dvas_analytics/:  python -m pytest anpr/tests -q
"""
import asyncio
import importlib
import sys
import types

import pytest


class FakeANPRAnalyzer:
    def __init__(self, *a, watchlist=None, **k):
        self.watchlist = watchlist or set()
        self.ready = True

    def update_watchlist(self, watchlist):
        self.watchlist = watchlist


@pytest.fixture
def anpr(monkeypatch):
    db_state = {"plates": {"KL07AB1234", "KL01X9"}, "fail": False, "added": []}

    async def get_active_watchlist_plates():
        if db_state["fail"]:
            raise ConnectionError("db down")
        return set(db_state["plates"])

    async def add_to_watchlist(plate, reason=None, case_ref=None):
        db_state["added"].append((plate, reason, case_ref))

    async def _noop(*a, **k):
        return None

    class FakePool:
        async def fetchrow(self, *a):
            return {"reason": "theft", "case_ref": "FIR/1"}

        async def execute(self, *a):
            db_state["theft_rows"] = db_state.get("theft_rows", 0) + 1

    async def get_pool():
        return FakePool()

    async def write_confirmed_event(**kw):
        db_state.setdefault("events", []).append(kw)
        return f"ev{len(db_state['events'])}"

    db = types.ModuleType("shared.db")
    db.get_active_watchlist_plates = get_active_watchlist_plates
    db.add_to_watchlist = add_to_watchlist
    db.get_pool = get_pool
    db.write_confirmed_event = write_confirmed_event
    db.__getattr__ = lambda attr: _noop
    other = {}
    for name in ("shared.redis_client", "shared.minio_client"):
        other[name] = types.ModuleType(name)
        other[name].__getattr__ = lambda attr: _noop
    analyzer_mod = types.ModuleType("anpr.analyzer")
    analyzer_mod.ANPRAnalyzer = FakeANPRAnalyzer

    for name, mod in {"cv2": types.ModuleType("cv2"), "shared.db": db,
                      "anpr.analyzer": analyzer_mod, **other}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "anpr.main", raising=False)
    main = importlib.import_module("anpr.main")

    async def _idle_loop():
        await asyncio.sleep(3600)
    monkeypatch.setattr(main, "_stream_loop", _idle_loop)
    yield main, db_state
    sys.modules.pop("anpr.main", None)


def _startup(main):
    async def go():
        await main.startup()
        main._stream_task.cancel()
        main._watchlist_task.cancel()
    asyncio.run(go())


def test_startup_loads_watchlist_from_db(anpr):
    main, _ = anpr
    _startup(main)
    assert main.analyzer.watchlist == {"KL07AB1234", "KL01X9"}


def test_startup_survives_db_error(anpr):
    main, state = anpr
    state["fail"] = True
    _startup(main)                       # must not raise
    assert main.analyzer.watchlist == set()


def test_reload_picks_up_db_changes(anpr):
    main, state = anpr
    _startup(main)
    state["plates"] = {"KL05ZZ1"}
    assert asyncio.run(main._reload_watchlist()) == 1
    assert main.analyzer.watchlist == {"KL05ZZ1"}


def test_add_persists_case_ref(anpr):
    main, state = anpr
    _startup(main)
    req = main.WatchlistAddRequest(plate_text=" kl07ab9 ", reason="theft", case_ref="FIR/2026/11")
    asyncio.run(main.watchlist_add(req))
    assert state["added"] == [(" kl07ab9 ", "theft", "FIR/2026/11")]
    assert "KL07AB9" in main.analyzer.watchlist



class _Upload:
    content_type = "image/jpeg"

    async def read(self):
        return b"frame-bytes"


def test_http_frame_only_watchlisted_plates_become_gated_theft_events(anpr, monkeypatch):
    main, state = anpr
    _startup(main)

    async def upload_object(bucket, key, data, content_type="", kind=""):
        return {"bucket": bucket, "key": key, "content_type": content_type, "sha256": "h",
                "bytes": len(data), "kind": kind}
    monkeypatch.setattr(main.minio, "upload_object", upload_object, raising=False)
    monkeypatch.setattr(main, "cv2", types.SimpleNamespace(imdecode=lambda *a: "frame", IMREAD_COLOR=1))
    plates = [{"plate_text": "KL07AB1234", "tracker_id": None, "ocr_confidence": 0.9, "is_reliable": True,
               "vehicle_type": "car", "is_watchlisted": True, "plate_crop_b64": "cGxhdGU="},
              {"plate_text": "KL01ZZ9", "tracker_id": None, "ocr_confidence": 0.8, "is_reliable": True,
               "vehicle_type": "car", "is_watchlisted": False}]
    main.analyzer.analyze_frame = lambda frame, **kw: {"plate_reads": [dict(p) for p in plates]}
    for _ in range(2):
        asyncio.run(main.analyze_frame(file=_Upload(), drone_id="D1"))
    events = state["events"]
    assert [e["event_type"] for e in events] == ["theft_alert"]          # once, and not "intrusion"
    objs = events[0]["attributes"]["evidence"]["objects"]
    assert [(o["kind"], o["role"]) for o in objs] == [("plate_crop", "primary"), ("source_frame", "source")]
    assert events[0]["attributes"]["case_ref"] == "FIR/1"
