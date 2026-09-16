# -*- coding: utf-8 -*-
"""
DVAS Analytics Gateway — FastAPI on port 8100.
Unified entry point for the dashboard:
  - SSE /events/stream  : multiplexes all analytics Redis streams
  - GET /health/all     : aggregated health from all 4 services
  - GET /summary        : unified stats snapshot
  - GET /dashboard/live : initial data load for dashboard
  - GET /metrics/prometheus : Prometheus text format
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any, Optional

import httpx
from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, get_pool, get_crowd_history,
    get_recent_confirmed_events, get_recent_plates,
)
from shared.redis_client import close_client, get_client
from shared.auth import verify_service_key

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("analytics-gateway")

app = FastAPI(
    title="DVAS Analytics Gateway",
    description="Unified SSE, health aggregation, and stats for the dashboard",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Service definitions
_SERVICES = {
    "crowd":     cfg.CROWD_URL,
    "person":    cfg.PERSON_URL,
    "vehicle":   cfg.VEHICLE_URL,
    "anpr":      cfg.ANPR_URL,
    "intrusion": cfg.INTRUSION_URL,
    "vessel":    cfg.VESSEL_URL,
}

# Redis stream names for each service
_ANALYTICS_STREAMS = [
    "analytics:crowd:results",
    "analytics:person:results",
    "analytics:person_alert:results",
    "analytics:vehicle:results",
    "analytics:anpr:results",
    "analytics:intrusion:results",
    "analytics:vessel:results",
    "drone:events:confirmed",
]


@app.on_event("startup")
async def startup():
    await get_pool()
    log.info("Analytics Gateway started on port 8100")


@app.on_event("shutdown")
async def shutdown():
    await close_pool()
    await close_client()


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "analytics-gateway", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/health/all")
async def health_all():
    """Poll all 4 analytics services and return aggregated health status."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, base_url in _SERVICES.items():
            try:
                resp = await client.get(f"{base_url}/health")
                results[name] = resp.json() if resp.status_code == 200 else {"status": "error", "code": resp.status_code}
            except Exception as exc:
                results[name] = {"status": "unreachable", "error": str(exc)}

    all_ok = all(v.get("status") == "ok" for v in results.values())
    return {
        "gateway":  "ok",
        "services": results,
        "overall":  "ok" if all_ok else "degraded",
        "ts":       datetime.now(timezone.utc).isoformat(),
    }


# ── Unified Summary ──────────────────────────────────────────────────────────

@app.get("/summary", dependencies=[Depends(verify_service_key)])
async def summary():
    """
    Unified real-time stats snapshot for the dashboard header cards.
    Combines data from Postgres across all 4 analytics domains.
    """
    pool = await get_pool()

    # Person count (active tracks in last 5 min)
    person_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM tracks WHERE track_type='person' "
        "AND last_seen > now() - interval '5 minutes'"
    )

    # Vehicle count (active tracks in last 5 min)
    vehicle_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM tracks WHERE track_type='vehicle' "
        "AND last_seen > now() - interval '5 minutes'"
    )

    # Plates read today
    plates_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM plate_reads WHERE created_at > now() - interval '24 hours'"
    )

    # Latest crowd density
    crowd_rows = await get_crowd_history(limit=1)
    crowd_count = crowd_rows[0]["estimated_count"] if crowd_rows else 0

    # Theft alerts today
    theft_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM theft_alerts WHERE detected_at > now() - interval '24 hours'"
    )

    # Recent confirmed events count
    events_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM confirmed_events WHERE ts > now() - interval '1 hour'"
    )

    # Active intrusion events (last 5 min)
    try:
        intrusion_row = await pool.fetchrow(
            "SELECT COUNT(*) as cnt FROM intrusion_events "
            "WHERE state_to IN ('ENTERING','INSIDE','DWELLING') "
            "AND ts > now() - interval '5 minutes'"
        )
        intrusion_count = intrusion_row["cnt"] if intrusion_row else 0
    except Exception:
        intrusion_count = 0  # Table may not exist yet

    # Active vessel tracks (last 5 min)
    try:
        vessel_row = await pool.fetchrow(
            "SELECT COUNT(*) as cnt FROM vessel_tracks "
            "WHERE last_seen > now() - interval '5 minutes'"
        )
        vessel_count = vessel_row["cnt"] if vessel_row else 0
    except Exception:
        vessel_count = 0

    return {
        "ts":                     datetime.now(timezone.utc).isoformat(),
        "active_persons":         person_row["cnt"] if person_row else 0,
        "active_vehicles":        vehicle_row["cnt"] if vehicle_row else 0,
        "active_vessels":         vessel_count,
        "plates_read_today":      plates_row["cnt"] if plates_row else 0,
        "current_crowd_count":    float(crowd_count),
        "theft_alerts_today":     theft_row["cnt"] if theft_row else 0,
        "events_last_hour":       events_row["cnt"] if events_row else 0,
        "active_intrusions":      intrusion_count,
    }


# ── Dashboard Live Data ───────────────────────────────────────────────────────

@app.get("/dashboard/live", dependencies=[Depends(verify_service_key)])
async def dashboard_live():
    """
    Combined live data snapshot for dashboard initial load.
    Returns recent events, crowd readings, plates, and active counts in one call.
    """
    pool = await get_pool()

    # Run all queries concurrently
    events_task  = get_recent_confirmed_events(limit=20)
    crowd_task   = get_crowd_history(limit=10)
    plates_task  = get_recent_plates(limit=10)
    summary_task = summary()

    events, crowd, plates, stats = await asyncio.gather(
        events_task, crowd_task, plates_task, summary_task
    )

    # Serialize datetime objects
    def _serialize(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    def _clean_rows(rows):
        return [{k: _serialize(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                 for k, v in r.items()} for r in rows]

    return {
        "ts":             datetime.now(timezone.utc).isoformat(),
        "summary":        stats,
        "recent_events":  _clean_rows(events),
        "crowd_readings": _clean_rows(crowd),
        "recent_plates":  _clean_rows(plates),
    }


@app.get("/events/confirmed")
async def list_confirmed_events(
    since: str = "",
    drone_id: str = "",
    limit: int = 100,
):
    """List recent confirmed events for evidence sync pipeline."""
    pool = await get_pool()
    
    query = "SELECT event_id::text, mission_id::text, drone_id, track_id::text, event_type, confidence, ts, latitude, longitude, attributes::text, status, created_at FROM confirmed_events"
    params = []
    conditions = []
    
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since_dt = datetime.now(timezone.utc)
        conditions.append(f"ts >= ${len(params) + 1}::timestamptz")
        params.append(since_dt)
    if drone_id:
        conditions.append(f"drone_id = ${len(params) + 1}")
        params.append(drone_id)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += f" ORDER BY ts DESC LIMIT ${len(params) + 1}"
    params.append(limit)
    
    rows = await pool.fetch(query, *params)
    
    events = []
    for r in rows:
        d = dict(r)
        # Convert non-serializable types
        for k, v in d.items():
            if hasattr(v, 'isoformat'):
                d[k] = v.isoformat()
            elif isinstance(v, (bytes, memoryview)):
                d[k] = str(v)
        # Parse attributes JSON string back to dict if present
        if d.get('attributes') and isinstance(d['attributes'], str):
            try:
                import json
                d['attributes'] = json.loads(d['attributes'])
            except Exception:
                pass
        events.append(d)
    
    return {"events": events, "count": len(events)}


# ── SSE Event Stream ─────────────────────────────────────────────────────────

@app.get("/events/stream", dependencies=[Depends(verify_service_key)])
async def events_stream(drone_id: Optional[str] = Query(default=None)):
    """
    SSE endpoint multiplexing all analytics Redis streams.
    The dashboard connects here to receive live events from all 4 services.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        client = get_client()
        # Start from latest ($ = only new messages after connection)
        last_ids: Dict[str, str] = {s: "$" for s in _ANALYTICS_STREAMS}

        try:
            while True:
                results = await client.xread(
                    last_ids,
                    count=10,
                    block=3000,
                )
                if results:
                    for stream_name, entries in results:
                        stream_key = stream_name.decode() if isinstance(stream_name, bytes) else stream_name
                        for entry_id, fields in entries:
                            last_ids[stream_key] = entry_id

                            data_str = fields.get("data", fields.get(b"data", "{}"))
                            if isinstance(data_str, bytes):
                                data_str = data_str.decode()

                            # Filter by drone_id if requested
                            if drone_id:
                                try:
                                    payload = json.loads(data_str)
                                    if payload.get("drone_id") and payload["drone_id"] != drone_id:
                                        continue
                                except Exception:
                                    pass

                            # Tag which service this came from
                            service_tag = stream_key.split(":")[1] if ":" in stream_key else stream_key
                            payload_obj = json.loads(data_str) if data_str else {}
                            envelope = json.dumps({
                                "stream":   service_tag,
                                "service":  service_tag,
                                "payload":  payload_obj,
                                "data":     payload_obj,
                                "drone_id": payload_obj.get("drone_id") if isinstance(payload_obj, dict) else None,
                            })
                            yield f"data: {envelope}\n\n"
                else:
                    # Keepalive every 3 seconds
                    yield f": keepalive {datetime.now(timezone.utc).isoformat()}\n\n"

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("SSE stream error: %s", exc)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ── Prometheus Metrics ────────────────────────────────────────────────────────

@app.get("/metrics/prometheus")
async def prometheus_metrics():
    """
    Prometheus-compatible text format metrics.
    Aggregates basic counters from Postgres for all 5 analytics services.
    """
    pool = await get_pool()

    person_row  = await pool.fetchrow("SELECT COUNT(*) as cnt FROM tracks WHERE track_type='person'")
    vehicle_row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM tracks WHERE track_type='vehicle'")
    events_row  = await pool.fetchrow("SELECT COUNT(*) as cnt FROM confirmed_events")
    plates_row  = await pool.fetchrow("SELECT COUNT(*) as cnt FROM plate_reads")
    crowd_rows  = await get_crowd_history(limit=1)
    crowd_count = crowd_rows[0]["estimated_count"] if crowd_rows else 0

    try:
        intrusion_row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM intrusion_events")
        intrusion_count = intrusion_row["cnt"] if intrusion_row else 0
    except Exception:
        intrusion_count = 0

    try:
        vessel_row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM vessel_tracks")
        vessel_count = vessel_row["cnt"] if vessel_row else 0
    except Exception:
        vessel_count = 0

    ts_ms = int(time.time() * 1000)
    lines = [
        "# HELP dvas_person_tracks_total Total person tracks",
        "# TYPE dvas_person_tracks_total counter",
        f"dvas_person_tracks_total {person_row['cnt'] if person_row else 0} {ts_ms}",
        "# HELP dvas_vehicle_tracks_total Total vehicle tracks",
        "# TYPE dvas_vehicle_tracks_total counter",
        f"dvas_vehicle_tracks_total {vehicle_row['cnt'] if vehicle_row else 0} {ts_ms}",
        "# HELP dvas_vessel_tracks_total Total vessel tracks",
        "# TYPE dvas_vessel_tracks_total counter",
        f"dvas_vessel_tracks_total {vessel_count} {ts_ms}",
        "# HELP dvas_confirmed_events_total Total confirmed events",
        "# TYPE dvas_confirmed_events_total counter",
        f"dvas_confirmed_events_total {events_row['cnt'] if events_row else 0} {ts_ms}",
        "# HELP dvas_plate_reads_total Total plate reads",
        "# TYPE dvas_plate_reads_total counter",
        f"dvas_plate_reads_total {plates_row['cnt'] if plates_row else 0} {ts_ms}",
        "# HELP dvas_crowd_count_current Latest crowd count estimate",
        "# TYPE dvas_crowd_count_current gauge",
        f"dvas_crowd_count_current {crowd_count} {ts_ms}",
        "# HELP dvas_intrusion_events_total Total intrusion state transitions",
        "# TYPE dvas_intrusion_events_total counter",
        f"dvas_intrusion_events_total {intrusion_count} {ts_ms}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8100, reload=False)
