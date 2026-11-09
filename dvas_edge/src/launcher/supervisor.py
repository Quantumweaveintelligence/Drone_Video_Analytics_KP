"""
src/launcher/supervisor.py
==========================
Runs the edge core as a child process without a console window and keeps it alive.

- Restart within RESTART_DELAY_S after an unexpected exit.
- Give up after MAX_CRASHES crashes inside CRASH_WINDOW_S (a crash loop must not hide a
  real fault behind endless restarts); the state becomes "failed" and the launcher shows it.
- Graceful stop: ask the core to shut down (it ends the mission and spills unsent frames),
  then terminate, then kill.
"""
from __future__ import annotations

import collections
import logging
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional

import httpx

log = logging.getLogger("launcher.supervisor")

RESTART_DELAY_S = 2.0
MAX_CRASHES = 3
CRASH_WINDOW_S = 300.0
GRACEFUL_STOP_S = 20.0
_POLL_S = 0.5
_CREATE_NO_WINDOW = 0x08000000


class CoreSupervisor:
    """Owns the edge core process. Thread-safe start / stop / restart."""

    def __init__(
        self,
        cmd: List[str],
        cwd: Path,
        env: Dict[str, str],
        core_url: str,
        stderr_log: Path,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        on_state: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cmd = cmd
        self._cwd = cwd
        self._secret = secrets.token_urlsafe(32)     # per launch; only this launcher can stop the core
        self._env = {**env, "DVAS_LAUNCHER_SECRET": self._secret}
        self._core_url = core_url
        self._stderr_log = stderr_log
        self._popen = popen
        self._clock = clock
        self._on_state = on_state or (lambda _s: None)
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._stopping = False
        self._crashes: Deque[float] = collections.deque()
        self.state = "stopped"
        self.restarts = 0
        self._watchdog: Optional[threading.Thread] = None

    def set_state_listener(self, listener: Callable[[str], None]) -> None:
        """Called with "running" / "restarting" / "failed" / "stopped" on every change."""
        self._on_state = listener

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            self._stopping = False
            self._spawn()
        if self._watchdog is None or not self._watchdog.is_alive():
            self._watchdog = threading.Thread(target=self._watch, name="CoreWatchdog", daemon=True)
            self._watchdog.start()

    def stop(self) -> None:
        """Graceful stop; returns when the core has exited."""
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc is None or proc.poll() is not None:
            self._set_state("stopped")
            return
        self._request_shutdown()
        try:
            proc.wait(timeout=GRACEFUL_STOP_S)
        except subprocess.TimeoutExpired:
            log.warning("core_did_not_exit_gracefully; terminating")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self._set_state("stopped")

    def restart(self) -> None:
        """Operator-requested restart: also clears a 'failed' state."""
        self.stop()
        with self._lock:
            self._crashes.clear()
        self.start()

    # ── internals ─────────────────────────────────────────────────────────────

    def _spawn(self) -> None:
        self._stderr_log.parent.mkdir(parents=True, exist_ok=True)
        stderr = open(self._stderr_log, "ab")     # tracebacks before core logging is set up
        flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = self._popen(
                self._cmd, cwd=str(self._cwd), env=self._env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                creationflags=flags,
            )
        finally:
            stderr.close()                         # the child keeps its own handle
        _kill_with_launcher(self._proc)
        log.info("core_started pid=%s", getattr(self._proc, "pid", "?"))
        self._set_state("running")

    def _watch(self) -> None:
        while True:
            time.sleep(_POLL_S)
            with self._lock:
                if self._stopping:
                    return
                proc = self._proc
                code = proc.poll() if proc is not None else None
                if code is None:
                    continue
                now = self._clock()
                self._crashes.append(now)
                while self._crashes and now - self._crashes[0] > CRASH_WINDOW_S:
                    self._crashes.popleft()
                if len(self._crashes) >= MAX_CRASHES:
                    log.error("core_crash_loop exit_code=%s crashes=%d window_s=%.0f; not restarting",
                              code, len(self._crashes), CRASH_WINDOW_S)
                    self._set_state("failed")
                    return
                log.error("core_exited_unexpectedly exit_code=%s; restarting in %.0f s", code, RESTART_DELAY_S)
                self._set_state("restarting")
            time.sleep(RESTART_DELAY_S)
            with self._lock:
                if self._stopping:
                    return
                self.restarts += 1
                self._spawn()

    def _request_shutdown(self) -> None:
        try:
            httpx.post(f"{self._core_url}/internal/shutdown",
                       headers={"X-Launcher-Secret": self._secret}, timeout=3.0)
        except httpx.HTTPError as exc:
            log.warning("core_shutdown_request_failed: %s", exc)

    def _set_state(self, state: str) -> None:
        self.state = state
        self._on_state(state)


_JOB = None   # one job object for the launcher's lifetime; closing it kills the children


def _kill_with_launcher(proc: subprocess.Popen) -> None:
    """Windows: put the core in a job object that is killed when the launcher exits.

    Without it, a crashed or killed launcher leaves an orphan core holding the port.
    """
    global _JOB
    if sys.platform != "win32" or not hasattr(proc, "_handle"):
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    if _JOB is None:
        job = k32.CreateJobObjectW(None, None)
        if not job:
            log.warning("job_object_unavailable error=%s", ctypes.get_last_error())
            return

        class _Limits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _ExtLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _Limits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = _ExtLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 = Extended
            log.warning("job_object_setup_failed error=%s", ctypes.get_last_error())
            return
        _JOB = job
    if not k32.AssignProcessToJobObject(_JOB, wintypes.HANDLE(int(proc._handle))):
        log.warning("job_object_assign_failed error=%s", ctypes.get_last_error())
