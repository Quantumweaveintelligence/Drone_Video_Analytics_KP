"""
core/runtime.py — process-runtime abstraction (RunPod plan Phase 2.3, finding F4).

The local stack runs every service as a Docker container and the backend
controls them through the mounted Docker socket. A RunPod pod is itself a
container, so there is no Docker daemon: every service runs as a supervisord
program inside one container. This module hides that difference.

    DVAS_RUNTIME=docker      (default) — Docker socket, behaviour unchanged
    DVAS_RUNTIME=supervisor  — supervisord XML-RPC over its unix socket

GPU telemetry is read through NVML (nvidia-ml-py) in supervisor mode instead
of `docker exec <analyzer> nvidia-smi`.
"""
from __future__ import annotations

import functools
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("dvas.runtime")

RUNTIME: str = os.getenv("DVAS_RUNTIME", "docker").strip().lower()
IS_SUPERVISOR: bool = RUNTIME == "supervisor"
SUPERVISOR_SOCKET: str = os.getenv("SUPERVISOR_SOCKET", "unix:///run/supervisor.sock")

# Compose container name -> supervisord program name (group:program for analytics).
CONTAINER_TO_PROGRAM: Dict[str, str] = {
    "drone_postgres": "postgres",
    "dvas_postgres": "postgres",
    "drone_valkey": "valkey",
    "drone_minio": "minio",
    "drone_keycloak": "keycloak",
    "drone_ingest_api": "ingest",
    "drone_nginx": "nginx",
    "dvas_backend": "backend",
    "dvas_frontend": "nginx",
    "drone_analytics_gateway": "analytics:gateway",
    "drone_crowd_analyzer": "analytics:crowd",
    "drone_person_analyzer": "analytics:person",
    "drone_vehicle_analyzer": "analytics:vehicle",
    "drone_anpr_analyzer": "analytics:anpr",
    "drone_intrusion_analyzer": "analytics:intrusion",
    "drone_vessel_analyzer": "analytics:vessel",
    "drone_anomaly_analyzer": "analytics:anomaly",
}

# Programs that must never be stopped from the UI in supervisor mode: stopping
# any of them cuts off the dashboard's own control plane (API, proxy, DB, auth).
PROTECTED_PROGRAMS = frozenset({"backend", "nginx", "postgres", "keycloak"})

# supervisord state name -> (docker-like status, health) as the UI expects them.
_STATE_MAP: Dict[str, Tuple[str, str]] = {
    "RUNNING": ("running", "healthy"),
    "STARTING": ("restarting", "starting"),
    "BACKOFF": ("restarting", "unhealthy"),
    "STOPPING": ("exited", "no_healthcheck"),
    "STOPPED": ("exited", "no_healthcheck"),
    "EXITED": ("exited", "no_healthcheck"),
    "FATAL": ("dead", "unhealthy"),
    "UNKNOWN": ("unknown", "missing"),
}

# supervisor.xmlrpc.Faults values (copied so docker-mode imports never need the package)
_FAULT_BAD_NAME = 10
_FAULT_ALREADY_STARTED = 60
_FAULT_NOT_RUNNING = 70


class RuntimeControlError(RuntimeError):
    """Raised when the process supervisor cannot be reached."""


def is_deployed(container_name: str) -> bool:
    """True when the container exists in the current runtime."""
    return (not IS_SUPERVISOR) or container_name in CONTAINER_TO_PROGRAM


def _supervisor():
    """Return an XML-RPC proxy bound to supervisord's unix socket."""
    try:
        from xmlrpc.client import ServerProxy  # noqa: PLC0415
        from supervisor.xmlrpc import SupervisorTransport  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency missing in image
        raise RuntimeControlError(f"supervisor package not installed: {exc}") from exc
    return ServerProxy("http://localhost", transport=SupervisorTransport(None, None, SUPERVISOR_SOCKET))


def _full_name(info: Dict[str, Any]) -> str:
    group, name = info.get("group", ""), info.get("name", "")
    return name if not group or group == name else f"{group}:{name}"


def get_process_table() -> Dict[str, Dict[str, Any]]:
    """Return supervisord process info keyed by full program name (blocking)."""
    try:
        infos = _supervisor().supervisor.getAllProcessInfo()
    except RuntimeControlError:
        raise
    except Exception as exc:
        raise RuntimeControlError(f"supervisord unreachable at {SUPERVISOR_SOCKET}: {exc}") from exc
    return {_full_name(i): i for i in infos}


def container_status(container_name: str, table: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Translate supervisord info for one compose container name into the UI shape."""
    program = CONTAINER_TO_PROGRAM.get(container_name)
    info = table.get(program or "")
    if info is None:
        return {"status": "not_found", "running": False, "health": "missing",
                "started_at": "", "image": "—"}
    status, health = _STATE_MAP.get(info.get("statename", "UNKNOWN"), ("unknown", "missing"))
    started = info.get("start") or 0
    started_at = datetime.fromtimestamp(started, tz=timezone.utc).isoformat() if started else ""
    return {
        "status": status,
        "running": status == "running",
        "health": health,
        "started_at": started_at,
        "image": f"supervisor:{program}",
    }


def _programs_for(container_names: Iterable[str], *, allow_protected: bool) -> Tuple[List[str], List[Dict[str, str]]]:
    """Map compose names to unique program names, dropping undeployed/protected ones."""
    programs: List[str] = []
    skipped: List[Dict[str, str]] = []
    for name in container_names:
        program = CONTAINER_TO_PROGRAM.get(name)
        if program is None:
            skipped.append({"container": name, "reason": "not_deployed"})
            continue
        if not allow_protected and program in PROTECTED_PROGRAMS:
            skipped.append({"container": name, "reason": "protected"})
            continue
        if program not in programs:
            programs.append(program)
    return programs, skipped


def control_programs(action: str, container_names: Iterable[str]) -> Dict[str, Any]:
    """Start / stop / restart the supervisord programs behind compose container names.

    Protected programs are skipped for every action (a restart of the backend
    from its own request would kill the request; nginx/postgres/keycloak would
    cut off the UI). Blocking — call through asyncio.to_thread.
    """
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"unsupported action: {action}")
    programs, skipped = _programs_for(container_names, allow_protected=False)
    rpc = _supervisor().supervisor
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for program in programs:
        try:
            if action in ("stop", "restart"):
                _call_ignoring(rpc.stopProcess, program, wait=True, ignore=(_FAULT_NOT_RUNNING,))
            if action in ("start", "restart"):
                _call_ignoring(rpc.startProcess, program, wait=False, ignore=(_FAULT_ALREADY_STARTED,))
            results.append({"container": program, "success": True})
        except Exception as exc:  # xmlrpc Fault, socket errors
            logger.error("supervisor %s %s failed: %s", action, program, exc)
            errors.append({"container": program, "error": str(exc)})
    return {"results": results, "errors": errors, "skipped": skipped}


def _call_ignoring(fn, program: str, *, wait: bool, ignore: Tuple[int, ...]) -> None:
    from xmlrpc.client import Fault  # noqa: PLC0415
    try:
        fn(program, wait)
    except Fault as fault:
        if fault.faultCode in ignore:
            return
        if fault.faultCode == _FAULT_BAD_NAME:
            raise RuntimeControlError(f"unknown supervisor program '{program}'") from fault
        raise


@functools.lru_cache(maxsize=1)
def gpu_name_nvml() -> Optional[str]:
    """Name of GPU 0 via NVML (e.g. 'NVIDIA RTX PRO 6000 Blackwell Server Edition'), or None."""
    try:
        import pynvml  # noqa: PLC0415  (package: nvidia-ml-py)
        pynvml.nvmlInit()
        try:
            name = pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
            return name.decode() if isinstance(name, bytes) else name
        finally:
            pynvml.nvmlShutdown()
    except Exception as exc:  # ImportError, NVMLError
        logger.debug("NVML name query failed: %s", exc)
        return None


def gpu_metrics_nvml() -> Optional[Tuple[int, float, float]]:
    """(util_pct, used_gb, total_gb) of GPU 0 via NVML, or None when unavailable."""
    try:
        import pynvml  # noqa: PLC0415  (package: nvidia-ml-py)
    except ImportError:
        logger.debug("nvidia-ml-py not installed; GPU telemetry unavailable")
        return None
    try:
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gib = 1024.0 ** 3
            return int(util.gpu), round(mem.used / gib, 1), round(mem.total / gib, 1)
        finally:
            pynvml.nvmlShutdown()
    except pynvml.NVMLError as exc:
        logger.debug("NVML query failed: %s", exc)
        return None
