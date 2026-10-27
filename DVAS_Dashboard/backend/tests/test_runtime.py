"""Unit tests for core/runtime.py (RunPod plan 2.3) — supervisord is mocked.

Run from DVAS_Dashboard/backend:  python -m pytest tests/test_runtime.py -q
"""
from xmlrpc.client import Fault

import pytest

from core import runtime


class FakeRPC:
    """Mimics supervisor's XML-RPC namespace."""

    def __init__(self, infos, faults=None):
        self.infos = infos
        self.faults = faults or {}
        self.calls = []

    def getAllProcessInfo(self):
        return self.infos

    def stopProcess(self, name, wait=True):
        self.calls.append(("stop", name))
        if ("stop", name) in self.faults:
            raise self.faults[("stop", name)]
        return True

    def startProcess(self, name, wait=True):
        self.calls.append(("start", name))
        if ("start", name) in self.faults:
            raise self.faults[("start", name)]
        return True


class FakeProxy:
    def __init__(self, rpc):
        self.supervisor = rpc


@pytest.fixture
def fake(monkeypatch):
    infos = [
        {"group": "postgres", "name": "postgres", "statename": "RUNNING", "start": 1_700_000_000},
        {"group": "ingest", "name": "ingest", "statename": "STOPPED", "start": 0},
        {"group": "analytics", "name": "crowd", "statename": "BACKOFF", "start": 1_700_000_100},
        {"group": "analytics", "name": "person", "statename": "FATAL", "start": 0},
    ]
    rpc = FakeRPC(infos)
    monkeypatch.setattr(runtime, "_supervisor", lambda: FakeProxy(rpc))
    return rpc


def test_process_table_uses_group_prefix(fake):
    table = runtime.get_process_table()
    assert set(table) == {"postgres", "ingest", "analytics:crowd", "analytics:person"}


def test_container_status_mapping(fake):
    table = runtime.get_process_table()
    pg = runtime.container_status("drone_postgres", table)
    assert pg["running"] and pg["status"] == "running" and pg["health"] == "healthy"
    assert pg["started_at"].startswith("2023-11-14")
    assert runtime.container_status("drone_crowd_analyzer", table)["health"] == "unhealthy"
    assert runtime.container_status("drone_person_analyzer", table)["status"] == "dead"
    # deployed name but program missing from supervisord → not_found
    assert runtime.container_status("drone_minio", table)["status"] == "not_found"


def test_is_deployed_only_filters_in_supervisor_mode(monkeypatch):
    monkeypatch.setattr(runtime, "IS_SUPERVISOR", True)
    assert runtime.is_deployed("drone_crowd_analyzer")
    assert not runtime.is_deployed("drone_headscale")
    monkeypatch.setattr(runtime, "IS_SUPERVISOR", False)
    assert runtime.is_deployed("drone_headscale")


def test_control_never_touches_protected_programs(fake):
    out = runtime.control_programs(
        "stop", ["drone_ingest_api", "drone_nginx", "dvas_backend", "drone_postgres",
                 "drone_keycloak", "drone_headscale", "drone_crowd_analyzer"])
    stopped = [name for verb, name in fake.calls]
    assert stopped == ["ingest", "analytics:crowd"]
    reasons = {s["container"]: s["reason"] for s in out["skipped"]}
    assert reasons["drone_nginx"] == "protected"
    assert reasons["drone_headscale"] == "not_deployed"
    assert out["errors"] == []


def test_restart_ignores_not_running_and_reports_real_errors(fake):
    fake.faults[("stop", "ingest")] = Fault(70, "NOT_RUNNING")
    fake.faults[("start", "analytics:crowd")] = Fault(50, "SPAWN_ERROR")
    out = runtime.control_programs("restart", ["drone_ingest_api", "drone_crowd_analyzer"])
    assert {"container": "ingest", "success": True} in out["results"]
    assert out["errors"] and out["errors"][0]["container"] == "analytics:crowd"


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        runtime.control_programs("kill", ["drone_ingest_api"])


def test_unreachable_supervisor_raises_runtime_error(monkeypatch):
    class Broken:
        class supervisor:  # noqa: N801
            @staticmethod
            def getAllProcessInfo():
                raise ConnectionRefusedError("no socket")
    monkeypatch.setattr(runtime, "_supervisor", lambda: Broken())
    with pytest.raises(runtime.RuntimeControlError):
        runtime.get_process_table()
