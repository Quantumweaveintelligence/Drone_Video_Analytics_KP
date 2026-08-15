"""
ingest_proxy.py
===============
Dashboard ↔ Ingest Server proxy routes.

Proxies requests from the dashboard frontend to the real DVAS ingest server
(FastAPI on the Ubuntu ingest host). This allows the dashboard to monitor,
query, and visualise the ingest pipeline without the frontend needing
direct access to the ingest server.

All routes require `system_administrator` role.

Routes
------
  GET  /api/ingest-server/health           → Ingest /health
  GET  /api/ingest-server/ready            → Ingest /health/ready
  GET  /api/ingest-server/status           → Ingest /ingest/status
  GET  /api/ingest-server/status/{drone}   → Ingest /ingest/status/{drone}
  GET  /api/ingest-server/metrics          → Ingest /metrics (raw Prometheus text)
  WS   /api/ingest-server/ws              → Relay to Ingest /ws/status
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

logger = logging.getLogger("dvas.ingest_proxy")

# ── Configuration ─────────────────────────────────────────────────────────────
INGEST_SERVER_URL = os.environ.get("INGEST_SERVER_URL", "http://localhost:8000")

# ── Router ────────────────────────────────────────────────────────────────────
ingest_api = APIRouter(prefix="/api/ingest-server", tags=["Ingest Server Proxy"])


async def _admin_user(request: Request):
    from server import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "system_administrator":
        raise HTTPException(403, "Role not permitted")
    return user


# ── Cached health state for the top-bar indicator ────────────────────────────
_last_health: dict = {"status": "unknown", "ts": 0}
_health_lock = asyncio.Lock()


async def _proxy_get(path: str, timeout: float = 5.0) -> dict:
    """Fire a GET to the ingest server and return the JSON response."""
    url = f"{INGEST_SERVER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(502, f"Ingest server unreachable at {INGEST_SERVER_URL}")
    except httpx.TimeoutException:
        raise HTTPException(504, "Ingest server timed out")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text)


async def _proxy_get_text(path: str, timeout: float = 5.0) -> str:
    """Fire a GET to the ingest server and return raw text."""
    url = f"{INGEST_SERVER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.ConnectError:
        raise HTTPException(502, f"Ingest server unreachable at {INGEST_SERVER_URL}")
    except httpx.TimeoutException:
        raise HTTPException(504, "Ingest server timed out")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text)


# ═════════════════════════════════════════════════════════════════════════════
# HEALTH PROXY
# ═════════════════════════════════════════════════════════════════════════════

@ingest_api.get("/health", summary="Proxy: ingest server liveness")
async def ingest_health():
    """
    Checks ingest server /health. Usable by ALL roles (powers the top-bar chip).
    No admin restriction — the HealthDot in AppShell polls this for every user.
    """
    global _last_health
    try:
        data = await _proxy_get("/health", timeout=3.0)
        result = {
            "reachable": True,
            "status": "ok",
            **data,
        }
        async with _health_lock:
            _last_health = {"status": "ok", "ts": time.time(), "data": data}
        return result
    except HTTPException:
        async with _health_lock:
            _last_health = {"status": "unreachable", "ts": time.time()}
        return {
            "reachable": False,
            "status": "unreachable",
            "detail": f"Ingest server at {INGEST_SERVER_URL} is not reachable",
        }


@ingest_api.get("/ready", summary="Proxy: ingest server readiness")
async def ingest_ready(user: dict = Depends(_admin_user)):
    """
    Checks ingest server /health/ready (Redis + Keycloak connectivity).
    Returns the readiness state including component health.
    """
    try:
        data = await _proxy_get("/health/ready", timeout=5.0)
        return {"reachable": True, **data}
    except HTTPException as exc:
        if exc.status_code == 503:
            # Ingest server reachable but not ready
            return {
                "reachable": True,
                "ready": False,
                "detail": "Ingest server reachable but downstream services unavailable",
            }
        return {
            "reachable": False,
            "ready": False,
            "detail": str(exc.detail),
        }


# ═════════════════════════════════════════════════════════════════════════════
# STATUS PROXY
# ═════════════════════════════════════════════════════════════════════════════

@ingest_api.get("/status", summary="Proxy: aggregate drone status")
async def ingest_status(user: dict = Depends(_admin_user)):
    """
    Proxies GET /ingest/status — returns queue depths, frame counters, and
    drone connection states for ALL tracked drones.
    """
    return await _proxy_get("/ingest/status")


@ingest_api.get("/status/{drone_id}", summary="Proxy: per-drone status")
async def ingest_drone_status(drone_id: str, user: dict = Depends(_admin_user)):
    """Proxies GET /ingest/status/{drone_id} — single drone detail."""
    return await _proxy_get(f"/ingest/status/{drone_id}")


# ═════════════════════════════════════════════════════════════════════════════
# METRICS PROXY
# ═════════════════════════════════════════════════════════════════════════════

@ingest_api.get("/metrics", summary="Proxy: Prometheus metrics")
async def ingest_metrics(user: dict = Depends(_admin_user)):
    """
    Proxies GET /metrics from the ingest server.
    Returns raw Prometheus text for parsing on the frontend.
    """
    text = await _proxy_get_text("/metrics")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=text, media_type="text/plain")


# ═════════════════════════════════════════════════════════════════════════════
# TELEMETRY PROXY (any authenticated role — used by command dashboard)
# ═════════════════════════════════════════════════════════════════════════════

@ingest_api.get("/telemetry", summary="Proxy: latest telemetry for all drones")
async def ingest_telemetry():
    """
    Returns the latest cached telemetry (GPS, battery, speed, etc.) for every
    drone that has sent at least one frame to the ingest server.
    Open to any authenticated role — used by the command dashboard map and
    multi-drone view, and by the backend telemetry pump to sync real drone data.
    NOTE: This is in-memory on the ingest server and clears on restart.
          Prefer /streams/active which reads directly from Valkey.
    """
    try:
        return await _proxy_get("/ingest/telemetry", timeout=3.0)
    except HTTPException:
        return {"drones": [], "count": 0}


@ingest_api.get("/streams/active", summary="Proxy: active drone streams from Valkey (restart-safe)")
async def ingest_streams_active():
    """
    Returns active drones with frames in the ingest pipeline.

    Fallback chain (most reliable → least):
      1. /ingest/streams/active  — Valkey-scan, restart-safe (new ingest server)
      2. /ingest/status          — in-memory, but live while server runs; filtered
                                   to drones with frames_received > 0
      3. /ingest/telemetry       — in-memory latest metadata (may be empty after restart)
    """
    # ── 1. New Valkey-scan endpoint ───────────────────────────────────────────
    try:
        result = await _proxy_get("/ingest/streams/active", timeout=5.0)
        if result.get("count", 0) > 0:
            # Fetch status to inject messages_ready
            try:
                status_data = await _proxy_get("/ingest/status", timeout=3.0)
                status_map = {d["drone_id"]: d.get("queue", {}).get("messages_ready", 0) for d in status_data.get("drones", [])}
                for d in result.get("drones", []):
                    d["messages_ready"] = status_map.get(d["drone_id"], 0)
            except Exception:
                pass
            return result
    except HTTPException:
        pass

    # ── 2. /ingest/status — filtered to online drones ────────────────────────
    try:
        status_data = await _proxy_get("/ingest/status", timeout=3.0)
        all_drones  = status_data.get("drones", [])
        # Filter by online=True (Valkey-backed) OR frames_received>0 (in-memory).
        # online=True is more reliable across multiple uvicorn workers since it
        # reflects Valkey stream existence rather than per-worker in-memory stats.
        active = [
            d for d in all_drones
            if d.get("online") is True or (d.get("frames_received") or 0) > 0
        ]
        if active:
            return {
                "drones": [
                    {
                        "drone_id":       d["drone_id"],
                        "frames_received": d.get("frames_received", 0),
                        "frame_count":     d.get("frames_received", 0),
                        "mission_id":      d.get("mission_id"),
                        "last_seen":       d.get("last_frame_at"),
                    }
                    for d in active
                ],
                "count": len(active),
            }
    except HTTPException:
        pass


    # ── 3. In-memory telemetry (may be empty after restart) ───────────────────
    try:
        return await _proxy_get("/ingest/telemetry", timeout=3.0)
    except HTTPException:
        return {"drones": [], "count": 0}


@ingest_api.get("/frame/{drone_id}/latest", summary="Proxy: latest JPEG frame for a drone")
async def ingest_latest_frame(drone_id: str):
    """
    Fetches the most recent JPEG frame stored in the ingest server's Valkey queue
    for the given drone and streams it back as image/jpeg.
    Used by the command dashboard multi-drone view (<img> polling every 500 ms).
    Open to any authenticated role.
    """
    url = f"{INGEST_SERVER_URL}/ingest/frame/{drone_id}/latest"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                raise HTTPException(404, f"No frames yet for drone {drone_id}")
            resp.raise_for_status()
            from fastapi.responses import Response
            return Response(
                content=resp.content,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(502, "Ingest server unreachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Ingest server timed out")
    except Exception as exc:
        logger.warning("Frame proxy error for %s: %s", drone_id, exc)
        raise HTTPException(500, "Frame retrieval failed")




# ═════════════════════════════════════════════════════════════════════════════
# DOCKER CONTAINER CONTROL
# Controls the ingest server containers via the host Docker socket.
# The backend container must have /var/run/docker.sock mounted (read-only
# for listing; full access for start/stop/restart).
# ═════════════════════════════════════════════════════════════════════════════

# Ordered list of all ingest stack containers (matches dvas_ingest docker-compose)
ALL_INGEST_CONTAINERS = [
    # Core ingest services (most critical — frame reception)
    "drone_ingest_api",
    "drone_nginx",
    # Identity & queues
    "drone_keycloak",
    "drone_valkey",
    # Database
    "drone_postgres",
    # Analytics workers
    "drone_gpu_consumer",
    "drone_crowd_analyzer",
    "drone_person_analyzer",
    "drone_vehicle_analyzer",
    "drone_anpr_analyzer",
    "drone_analytics_gateway",
    # Evidence storage
    "drone_seaweed_master",
    "drone_seaweed_volume",
    "drone_seaweed_filer",
    # Monitoring
    "drone_prometheus",
    "drone_grafana",
    "drone_alertmanager",
    # VPN / PKI
    "drone_headscale",
    "drone_step_ca",
]

# "Core" scope: stops/starts only the frame-reception layer
# DB, monitoring, and storage remain untouched for safe maintenance
INGEST_CORE_CONTAINERS = ["drone_ingest_api", "drone_nginx"]


def _docker_client():
    """Return a Docker client via the mounted socket. Raises 503 if unavailable."""
    try:
        import docker  # noqa: PLC0415
        return docker.from_env()
    except Exception as exc:
        raise HTTPException(503, f"Docker socket unavailable: {exc}")


@ingest_api.get("/containers", summary="List ingest container statuses")
async def list_containers(user: dict = Depends(_admin_user)):
    """
    Returns the live status of every container in the dvas_ingest stack.
    Used by the admin control panel to show a container health grid.
    """
    client = _docker_client()
    result = []
    try:
        for name in ALL_INGEST_CONTAINERS:
            try:
                c = client.containers.get(name)
                attrs = c.attrs or {}
                state = attrs.get("State", {})
                result.append({
                    "name": name,
                    "short_name": name.replace("drone_", ""),
                    "status": c.status,           # running | exited | paused | …
                    "running": c.status == "running",
                    "started_at": state.get("StartedAt", ""),
                    "image": c.image.tags[0] if c.image.tags else "—",
                })
            except Exception:
                result.append({
                    "name": name,
                    "short_name": name.replace("drone_", ""),
                    "status": "not_found",
                    "running": False,
                    "started_at": "",
                    "image": "—",
                })
    finally:
        client.close()
    return result


@ingest_api.post("/control", summary="Start / Stop / Restart ingest server")
async def control_ingest(body: dict, user: dict = Depends(_admin_user)):
    """
    Perform a lifecycle action on the ingest server containers.

    Request body::

        {
            "action": "start" | "stop" | "restart",
            "scope":  "core"  | "all"
        }

    - **core** (default) — only ``drone_ingest_api`` + ``drone_nginx``.
      Database, monitoring, and analytics workers keep running.
      Use for routine maintenance / security lockdown.

    - **all** — all 18 containers in the ingest stack.
      Use only for full system shutdown / upgrade.

    All actions are logged with the operator's e-mail for audit.
    """
    act = (body.get("action") or "").lower()
    scope = (body.get("scope") or "core").lower()

    if act not in ("start", "stop", "restart"):
        raise HTTPException(400, "action must be one of: start, stop, restart")
    if scope not in ("core", "all"):
        raise HTTPException(400, "scope must be one of: core, all")

    targets = INGEST_CORE_CONTAINERS if scope == "core" else ALL_INGEST_CONTAINERS
    # For "all" stop, reverse order (analytics → ingest → infra) for clean shutdown
    if act == "stop" and scope == "all":
        targets = list(reversed(targets))

    client = _docker_client()
    results, errors = [], []
    try:
        for name in targets:
            try:
                c = client.containers.get(name)
                if act == "stop":
                    c.stop(timeout=15)
                elif act == "start":
                    c.start()
                elif act == "restart":
                    c.restart(timeout=15)
                results.append({"container": name, "success": True})
            except Exception as exc:
                errors.append({"container": name, "error": str(exc)})
    finally:
        client.close()

    operator = user.get("email", user.get("sub", "unknown"))
    logger.info(
        "Ingest control: operator=%s action=%s scope=%s targets=%d errors=%d",
        operator, act, scope, len(results), len(errors),
    )

    return {
        "action": act,
        "scope": scope,
        "success": len(errors) == 0,
        "results": results,
        "errors": errors,
    }


# ═════════════════════════════════════════════════════════════════════════════
# WEBSOCKET RELAY
# ═════════════════════════════════════════════════════════════════════════════

@ingest_api.websocket("/ws")
async def ingest_ws_relay(ws: WebSocket):
    """
    WebSocket relay: dashboard frontend ↔ ingest server /ws/status.

    The dashboard frontend connects here; this endpoint connects to the
    real ingest server's WebSocket and relays messages in both directions.
    On disconnect from either end, both sides are closed.
    """
    await ws.accept()

    # Auth check — extract token from query param
    from server import _authz_ws
    token = ws.query_params.get("token", "")
    payload = _authz_ws(token)
    if not payload or payload.get("role") != "system_administrator":
        await ws.close(code=4403)
        return

    ingest_ws_url = INGEST_SERVER_URL.replace("http://", "ws://").replace("https://", "wss://")
    ingest_ws_url = f"{ingest_ws_url}/ws/status"

    try:
        import websockets
        async with websockets.connect(ingest_ws_url) as upstream:
            async def relay_upstream_to_client():
                """Forward messages from ingest server → dashboard client."""
                try:
                    async for msg in upstream:
                        try:
                            await ws.send_text(msg)
                        except Exception:
                            break
                except Exception:
                    pass

            async def relay_client_to_upstream():
                """Forward messages from dashboard client → ingest server."""
                try:
                    while True:
                        data = await ws.receive_text()
                        await upstream.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            # Run both relay directions concurrently
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(relay_upstream_to_client()),
                    asyncio.create_task(relay_client_to_upstream()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.warning(f"Ingest WS relay failed: {exc}")
        try:
            await ws.send_text(json.dumps({
                "event": "relay_error",
                "detail": f"Cannot connect to ingest server WebSocket: {exc}",
            }))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
