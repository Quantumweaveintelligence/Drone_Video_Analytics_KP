"""
system_telemetry.py
===================
Real system telemetry & NOC operations metrics provider for DVAS.
Replaces mock / random data in the Overview page with live end-to-end telemetry.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select, func

from database import async_session_factory
from models import Drone, AuditLog, Evidence
from core.config import INGEST_URL, CROWD_URL, KEYCLOAK_URL, KEYCLOAK_REALM
from ingest_proxy import _proxy_get, _docker_client
from core import runtime

logger = logging.getLogger("dvas.telemetry")

# ── In-memory rolling history buffers ─────────────────────────────────────────
_SPARKLINE_BUFFER: collections.deque = collections.deque(maxlen=30)
_TREND_BUFFER: collections.deque = collections.deque(maxlen=24)
_API_LATENCIES: Dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=20))
_API_OUTCOMES: Dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=20))
_LAST_SAMPLE_TIME: float = 0.0
_LAST_NET_BYTES: int = 0


def _get_cpu_percent() -> Optional[float]:
    """Calculate real host CPU percent via /proc/stat delta."""
    try:
        with open("/proc/stat", "r") as f:
            fields = [float(x) for x in f.readline().strip().split()[1:5]]
        idle1, total1 = fields[3], sum(fields)
        time.sleep(0.04)
        with open("/proc/stat", "r") as f:
            fields = [float(x) for x in f.readline().strip().split()[1:5]]
        idle2, total2 = fields[3], sum(fields)
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta <= 0:
            return None
        return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)
    except Exception as exc:
        logger.debug("Error reading /proc/stat: %s", exc)
        return None


def _get_ram_metrics() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate real host RAM usage (pct, total_gb, used_gb) via /proc/meminfo."""
    try:
        with open("/proc/meminfo", "r") as f:
            mem = {
                line.split(":")[0].strip(): int(line.split(":")[1].split()[0])
                for line in f if ":" in line
            }
        total_kb = mem["MemTotal"]
        avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
        used_kb = total_kb - avail_kb
        pct = round(max(0.0, min(100.0, used_kb / total_kb * 100.0)), 1)
        total_gb = round(total_kb / (1024.0 * 1024.0), 1)
        used_gb = round(used_kb / (1024.0 * 1024.0), 1)
        return pct, total_gb, used_gb
    except Exception as exc:
        logger.debug("Error reading /proc/meminfo: %s", exc)
        return None, None, None


def _get_disk_metrics() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate real disk usage (pct, total_gb, used_gb)."""
    try:
        # In the RunPod pod the persistent data lives on the network volume.
        u = shutil.disk_usage(os.getenv("DVAS_DISK_PATH", "/"))
        pct = round(max(0.0, min(100.0, u.used / u.total * 100.0)), 1)
        total_gb = round(u.total / (1024.0 ** 3), 1)
        used_gb = round(u.used / (1024.0 ** 3), 1)
        return pct, total_gb, used_gb
    except Exception as exc:
        logger.debug("Error reading disk usage: %s", exc)
        return None, None, None


def _get_gpu_metrics() -> Tuple[Optional[int], Optional[float], Optional[float]]:
    """
    Query real NVIDIA GPU metrics (util_pct, used_gb, total_gb).

    supervisor runtime (RunPod pod): NVML directly (nvidia-ml-py).
    docker runtime (local compose): docker exec nvidia-smi on drone_crowd_analyzer.
    """
    if runtime.IS_SUPERVISOR:
        return runtime.gpu_metrics_nvml() or (None, None, None)
    try:
        client = _docker_client()
        c = client.containers.get("drone_crowd_analyzer")
        res = c.exec_run(
            "nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.total,memory.used --format=csv,noheader,nounits",
            demux=False,
        )
        client.close()
        out = res.output.decode("utf-8", errors="ignore").strip()
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 4:
            gpu_pct = int(parts[0])
            total_mb = float(parts[2])
            used_mb = float(parts[3])
            return gpu_pct, round(used_mb / 1024.0, 1), round(total_mb / 1024.0, 1)
    except Exception as exc:
        logger.debug("nvidia-smi query via docker failed: %s", exc)
    return None, None, None


def _get_network_throughput_mbps() -> Optional[float]:
    """Network throughput from a /proc/net/dev delta; None on the first sample or when unreadable."""
    global _LAST_NET_BYTES, _LAST_SAMPLE_TIME
    try:
        total_bytes = 0
        with open("/proc/net/dev", "r") as f:
            for line in f.readlines()[2:]:
                parts = line.split()
                if len(parts) >= 10:
                    total_bytes += int(parts[1]) + int(parts[9])
    except Exception:
        return None
    now = time.time()
    mbps = None
    if _LAST_SAMPLE_TIME > 0 and now > _LAST_SAMPLE_TIME and total_bytes >= _LAST_NET_BYTES:
        mbps = round((total_bytes - _LAST_NET_BYTES) * 8.0 / ((now - _LAST_SAMPLE_TIME) * 1_000_000.0), 2)
    _LAST_NET_BYTES = total_bytes
    _LAST_SAMPLE_TIME = now
    return mbps


# ── High-level Collectors ─────────────────────────────────────────────────────

async def collect_system_hardware() -> Dict[str, Any]:
    """Collect real CPU, RAM, GPU, Disk, and Network telemetry."""
    loop = asyncio.get_running_loop()
    cpu_pct = await loop.run_in_executor(None, _get_cpu_percent)
    ram_pct, ram_total, ram_used = await loop.run_in_executor(None, _get_ram_metrics)
    disk_pct, disk_total, disk_used = await loop.run_in_executor(None, _get_disk_metrics)
    gpu_pct, gpu_used, gpu_total = await loop.run_in_executor(None, _get_gpu_metrics)
    net_mbps = await loop.run_in_executor(None, _get_network_throughput_mbps)

    return {
        "cpu_pct": cpu_pct,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "gpu_pct": gpu_pct,
        "gpu_mem_used_gb": gpu_used,
        "gpu_mem_total_gb": gpu_total,
        "disk_pct": disk_pct,
        "disk_used_gb": disk_used,
        "disk_total_gb": disk_total,
        "network_mbps": net_mbps,
        "network_cap_mbps": None,
    }


_STATUS_TTL_S = 2.0
_LIVE_WINDOW_S = 15.0       # a feed is live while its last frame is younger than this
_SYNC_WINDOW_S = 5.0        # ...and in sync while younger than this
_status_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_status_lock = asyncio.Lock()
# drone_id → (sample_time, frames_received, bytes_ingested) from the previous ingest poll
_PREV_COUNTERS: Dict[str, Tuple[float, int, int]] = {}
# drone_id → {"fps": float, "bitrate_mbps": float} measured between the last two polls
_RATES: Dict[str, Dict[str, float]] = {}


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _update_rates(drones: List[Dict[str, Any]], now: float) -> None:
    """Frame rate and bitrate from counter deltas between two ingest polls."""
    for d in drones:
        did = d.get("drone_id")
        if not did:
            continue
        st = d.get("ingest_stats") or {}
        frames, nbytes = int(st.get("frames_received") or 0), int(st.get("bytes_ingested") or 0)
        prev = _PREV_COUNTERS.get(did)
        _PREV_COUNTERS[did] = (now, frames, nbytes)
        if prev is None or now <= prev[0] or frames < prev[1]:
            _RATES.pop(did, None)          # first sample, or the ingest server restarted
            continue
        dt = now - prev[0]
        _RATES[did] = {
            "fps": round((frames - prev[1]) / dt, 1),
            "bitrate_mbps": round(max(0, nbytes - prev[2]) * 8.0 / (dt * 1_000_000.0), 2),
        }


async def _ingest_status() -> Optional[Dict[str, Any]]:
    """GET /ingest/status once per poll (shared by the collectors); None when unreachable."""
    async with _status_lock:
        now = time.time()
        if now - _status_cache["ts"] < _STATUS_TTL_S:
            return _status_cache["data"]
        try:
            data = await _proxy_get("/ingest/status", timeout=2.5)
            data = data if isinstance(data, dict) else None
        except Exception as exc:
            logger.debug("Could not fetch ingest /status: %s", exc)
            data = None
        if data is not None:
            _update_rates(data.get("drones", []), now)
        _status_cache.update(ts=now, data=data)
        return data


async def collect_stream_overview() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Drones from PostgreSQL merged with the live ingest counters. Rates are measured
    between polls; values that are not measured are None (never a nominal default).
    """
    async with async_session_factory() as session:
        result = await session.execute(select(Drone).limit(200))
        db_map = {d.id: d for d in result.scalars().all()}

    status = await _ingest_status()
    ingest_drones = {d["drone_id"]: d for d in (status or {}).get("drones", []) if d.get("drone_id")}
    now = datetime.now(timezone.utc)

    streams = []
    for did in dict.fromkeys(list(db_map) + list(ingest_drones)):
        db_d = db_map.get(did)
        ing_d = ingest_drones.get(did, {})
        stats = ing_d.get("ingest_stats") or {}
        received, rejected = stats.get("frames_received", 0), stats.get("frames_rejected", 0)

        last_frame = _parse_ts(stats.get("last_frame_at"))
        frame_age_s = round((now - last_frame).total_seconds(), 1) if last_frame else None
        if status is not None:
            live = frame_age_s is not None and frame_age_s <= _LIVE_WINDOW_S
            sync = ("synced" if frame_age_s <= _SYNC_WINDOW_S else "drift") if live else "lost"
        else:
            # Ingest server unreachable: report the last state the database knows, unmeasured.
            live, sync = bool(db_d and db_d.status == "live"), None

        rates = _RATES.get(did, {}) if live else {}
        streams.append({
            "drone_id": did,
            "operator": (db_d.operator_name if db_d else None) or None,
            "mission_id": (db_d.mission_id if db_d else None) or ing_d.get("mission_id") or None,
            "fps": rates.get("fps") if live else 0.0,
            "bitrate_mbps": rates.get("bitrate_mbps") if live else 0.0,
            "frame_age_s": frame_age_s,
            "last_frame_at": last_frame.isoformat() if last_frame else None,
            "latency_ms": None,   # end-to-end latency is not instrumented
            "packet_drop_pct": round(rejected / received * 100.0, 2) if received else None,
            "sync": sync,
            "status": "live" if live else ((db_d.status if db_d else None) or "offline"),
        })

    live_streams = [s for s in streams if s["status"] == "live"]
    fps_vals = [s["fps"] for s in live_streams if s["fps"] is not None]
    ages = [s["frame_age_s"] for s in live_streams if s["frame_age_s"] is not None]
    stream_stats = {
        "active": len(live_streams),
        "warning": sum(1 for s in live_streams if s["sync"] == "drift"),
        "critical": sum(1 for s in streams if s["status"] == "offline"),
        "authenticated": len(live_streams),
        "avg_latency_ms": None,
        "avg_frame_age_s": round(sum(ages) / len(ages), 1) if ages else None,
        "avg_fps": round(sum(fps_vals) / len(fps_vals), 1) if fps_vals else None,
        "ingest_reachable": status is not None,
    }
    return streams, stream_stats


async def collect_queue_metrics() -> List[Dict[str, Any]]:
    """
    Queue figures that are actually measured: ingest depth from RabbitMQ (via the
    ingest server), evidence and audit write rates from the database. The detection
    queue is not instrumented, so its figures are None.
    """
    status = await _ingest_status()
    ingest = {"name": "ingest", "depth": None, "backlog": None, "throughput_fps": None,
              "throughput_unit": "frames/s", "error_rate_pct": None, "source": "ingest server"}
    if status is not None:
        drones = status.get("drones", [])
        received = sum((d.get("ingest_stats") or {}).get("frames_received", 0) for d in drones)
        rejected = sum((d.get("ingest_stats") or {}).get("frames_rejected", 0) for d in drones)
        ingest.update(
            depth=sum((d.get("queue") or {}).get("messages_ready", 0) for d in drones),
            throughput_fps=round(sum(r["fps"] for r in _RATES.values()), 1) if _RATES else None,
            error_rate_pct=round(rejected / received * 100.0, 2) if received else None,
        )

    evidence_hr = audit_hr = None
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    async with async_session_factory() as session:
        try:
            evidence_hr = (await session.execute(
                select(func.count(Evidence.id)).where(Evidence.ts >= hour_ago))).scalar() or 0
            audit_hr = (await session.execute(
                select(func.count(AuditLog.id)).where(AuditLog.ts >= hour_ago.isoformat()))).scalar() or 0
        except Exception as exc:
            logger.debug("queue metrics query failed: %s", exc)

    return [
        ingest,
        {"name": "detection", "depth": None, "backlog": None, "throughput_fps": None,
         "throughput_unit": None, "error_rate_pct": None, "source": "not instrumented"},
        {"name": "evidence", "depth": None, "backlog": None, "throughput_fps": evidence_hr,
         "throughput_unit": "records/h", "error_rate_pct": None, "source": "database"},
        {"name": "audit", "depth": None, "backlog": None, "throughput_fps": audit_hr,
         "throughput_unit": "entries/h", "error_rate_pct": None, "source": "database"},
    ]


async def collect_frame_ingestion() -> Dict[str, Any]:
    """Frame counters of the ingest server (cumulative since it last started) and an FPS sparkline."""
    status = await _ingest_status()
    if status is None:
        return {"window": "since ingest server start", "available": False,
                "total_frames": None, "kept_frames": None, "dropped_frames": None,
                "selection_ratio_pct": None, "drop_ratio_pct": None, "sparkline_fps": list(_SPARKLINE_BUFFER)}
    drones = status.get("drones", [])
    total = sum((d.get("ingest_stats") or {}).get("frames_received", 0) for d in drones)
    dropped = sum((d.get("ingest_stats") or {}).get("frames_rejected", 0) for d in drones)
    kept = int(status.get("total_frames_ingested", total - dropped))
    if _RATES:
        _SPARKLINE_BUFFER.append(round(sum(r["fps"] for r in _RATES.values()), 1))
    return {
        "window": "since ingest server start",
        "available": True,
        "total_frames": total,
        "kept_frames": kept,
        "dropped_frames": dropped,
        "selection_ratio_pct": round(kept / total * 100.0, 2) if total else None,
        "drop_ratio_pct": round(dropped / total * 100.0, 3) if total else None,
        "sparkline_fps": list(_SPARKLINE_BUFFER),
    }


def _valid_gps(d: Drone) -> bool:
    return (d.lat is not None and d.lng is not None and -90 <= d.lat <= 90 and -180 <= d.lng <= 180
            and not (d.lat == 0 and d.lng == 0))


def _valid_sensors(d: Drone) -> bool:
    return (d.battery_pct is not None and 0 <= d.battery_pct <= 100
            and (d.altitude_m is None or -100 <= d.altitude_m <= 10_000)
            and (d.speed_mps is None or 0 <= d.speed_mps <= 150)
            and (d.signal_pct is None or 0 <= d.signal_pct <= 100))


async def collect_metadata_validation() -> Dict[str, Any]:
    """
    Validity of the telemetry currently held for each drone: GPS and sensor fields
    from the drone rows, timestamps from the ingest server's last-frame times.
    """
    async with async_session_factory() as session:
        drones = (await session.execute(select(Drone))).scalars().all()
    status = await _ingest_status()
    now = datetime.now(timezone.utc)
    stamps = [(d.get("ingest_stats") or {}).get("last_frame_at") for d in (status or {}).get("drones", [])]
    stamps = [s for s in stamps if s]
    good_ts = sum(1 for s in stamps if (t := _parse_ts(s)) is not None and t <= now + timedelta(seconds=30))

    def pct(good: int, n: int) -> Optional[float]:
        return round(good / n * 100.0, 1) if n else None

    return {
        "gps_valid_pct": pct(sum(_valid_gps(d) for d in drones), len(drones)),
        "sensor_valid_pct": pct(sum(_valid_sensors(d) for d in drones), len(drones)),
        "timestamp_valid_pct": pct(good_ts, len(stamps)),
        "records_checked": len(drones),
        "timestamps_checked": len(stamps),
        "scope": "current telemetry of each registered drone",
    }


async def _probe_db() -> bool:
    async with async_session_factory() as session:
        await session.execute(select(1))
    return True


async def probe_api_monitor() -> Dict[str, Any]:
    """
    Probe the services the platform depends on. Success % and p95 are computed over the
    last 20 probes of each service; request rates are not instrumented (None).
    """
    targets = [
        ("ingest",        f"{INGEST_URL}/health"),
        ("crowd-analyzer", f"{CROWD_URL}/health"),
        ("keycloak",      f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"),
        ("database",      None),
    ]
    results = []
    async with httpx.AsyncClient(timeout=1.5) as client:
        for name, url in targets:
            t0 = time.time()
            try:
                if url:
                    ok = (await client.get(url)).status_code < 500
                else:
                    ok = await asyncio.wait_for(_probe_db(), timeout=1.5)
            except Exception:
                ok = False
            _API_LATENCIES[name].append(round((time.time() - t0) * 1000.0))
            _API_OUTCOMES[name].append(ok)
            lats = sorted(_API_LATENCIES[name])
            outcomes = _API_OUTCOMES[name]
            results.append({
                "path": name,
                "ok": ok,
                "rps": None,
                "p95_ms": lats[min(len(lats) - 1, int(len(lats) * 0.95))],
                "success_pct": round(sum(outcomes) / len(outcomes) * 100.0, 1),
                "samples": len(outcomes),
            })

    return {
        "endpoints": results,
        "total_rps": None,
        "avg_success_pct": round(sum(e["success_pct"] for e in results) / len(results), 1),
        "avg_p95_ms": round(sum(e["p95_ms"] for e in results) / len(results)),
    }


def update_trend_history(cpu_pct: Optional[float], gpu_pct: Optional[float]) -> List[Dict[str, Any]]:
    """Rolling history (up to 24 samples) of measured CPU and GPU percentages; no backfill."""
    _TREND_BUFFER.append({"ts": datetime.now(timezone.utc).isoformat(), "cpu": cpu_pct, "gpu": gpu_pct})
    return [{"t": i, **pt} for i, pt in enumerate(_TREND_BUFFER)]
