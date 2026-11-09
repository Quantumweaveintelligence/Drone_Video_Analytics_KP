"""Core supervision: restart, crash-loop stop, graceful shutdown, no orphan core."""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import src.launcher.supervisor as sup
from src.launcher.supervisor import CoreSupervisor

# A minimal "core": serves POST /internal/shutdown with the launcher secret, then exits 0.
_FAKE_CORE = r"""
import os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
secret = os.environ["DVAS_LAUNCHER_SECRET"]
port = int(sys.argv[1])
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        ok = self.path == "/internal/shutdown" and self.headers.get("X-Launcher-Secret") == secret
        self.send_response(200 if ok else 403); self.end_headers()
        if ok:
            open(sys.argv[2], "w").write("graceful")
            raise SystemExit(0)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(sup, "RESTART_DELAY_S", 0.1)
    monkeypatch.setattr(sup, "_POLL_S", 0.05)


def _wait(cond, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_crash_loop_stops_after_three_crashes(tmp_path: Path) -> None:
    states = []
    s = CoreSupervisor([sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path, {}, "http://127.0.0.1:1",
                       tmp_path / "err.log", on_state=states.append)
    s.start()
    assert _wait(lambda: s.state == "failed")
    assert s.restarts == 2                      # started once + 2 restarts = 3 crashes
    assert "restarting" in states


def test_restart_after_unexpected_exit(tmp_path: Path) -> None:
    marker = tmp_path / "runs"
    code = f"open(r'{marker}', 'a').write('x'); import time, sys; time.sleep(0.3 if len(open(r'{marker}').read()) == 1 else 30)"
    s = CoreSupervisor([sys.executable, "-c", code], tmp_path, {}, "http://127.0.0.1:1", tmp_path / "err.log")
    s.start()
    assert _wait(lambda: marker.exists() and len(marker.read_text()) == 2)
    assert s.state == "running" and s.restarts == 1
    s.stop()


def test_graceful_stop_uses_secret(tmp_path: Path) -> None:
    port, flag = _free_port(), tmp_path / "flag"
    script = tmp_path / "core.py"
    script.write_text(_FAKE_CORE, encoding="utf-8")
    s = CoreSupervisor([sys.executable, str(script), str(port), str(flag)], tmp_path, {"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
                       f"http://127.0.0.1:{port}", tmp_path / "err.log")
    s.start()
    assert _wait(lambda: socket.socket().connect_ex(("127.0.0.1", port)) == 0)
    s.stop()
    assert flag.read_text() == "graceful" and s.state == "stopped"


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_core_dies_when_launcher_is_killed(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    root = str(Path(__file__).resolve().parents[1])
    launcher = (
        f"import sys, time; sys.path.insert(0, {root!r})\n"
        "from pathlib import Path\n"
        "from src.launcher.supervisor import CoreSupervisor\n"
        f"s = CoreSupervisor([sys.executable, '-c', 'import time; time.sleep(60)'], Path(r'{tmp_path}'), dict(__import__('os').environ), 'http://127.0.0.1:1', Path(r'{tmp_path / 'e.log'}'))\n"
        "s.start()\n"
        f"open(r'{pid_file}', 'w').write(str(s._proc.pid))\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", launcher])
    assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
    core_pid = int(pid_file.read_text())
    proc.kill()                                  # launcher dies without any cleanup
    proc.wait()

    def core_gone() -> bool:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {core_pid}"], capture_output=True, text=True).stdout
        return str(core_pid) not in out
    assert _wait(core_gone, timeout=10)
