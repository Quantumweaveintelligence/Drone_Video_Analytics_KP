"""
DVAS Edge launcher: static UI + local edge core + cloud API reverse proxy
=========================================================================
Binds 127.0.0.1:8765 (with dynamic port fallback) and provides:
  /                     → static edge React build (REACT_APP_DEPLOYMENT_TARGET=edge)
  /edge-launcher/health → launcher health & single-instance check
  /edge-api/*           → edge core on 127.0.0.1:8766 (or dynamic core port)
  /api/*                → cloud backend (DVAS_CLOUD_URL)
  /api/ws (WS)          → cloud WebSocket (wss://...)

This reproduces exactly what the dashboard nginx does today on a single
origin, so the React code needs NO URL changes.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import socket
import subprocess
import sys
import threading
import webbrowser

import httpx
import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

# ── Paths & Config ────────────────────────────────────────────────────────────
APP_DIR  = pathlib.Path(__file__).resolve().parent
DATA_DIR = pathlib.Path(os.environ.get("LOCALAPPDATA", str(APP_DIR))) / "DVAS_Edge"

# Load edge.env from the data directory
_env_file = DATA_DIR / "config" / "edge.env"
if _env_file.exists():
    load_dotenv(_env_file)

CLOUD    = os.environ.get("DVAS_CLOUD_URL", "http://localhost:3000").rstrip("/")
CORE     = "http://127.0.0.1:8766"
STATIC   = APP_DIR / "static"

# Hop-by-hop headers that must NOT be forwarded
HOP = {
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "te", "trailer",
}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# ── Port & Instance Helpers ───────────────────────────────────────────────────
def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_free_port(start_port: int, host: str = "127.0.0.1", max_attempts: int = 50) -> int:
    """Find the next available port starting from start_port."""
    for p in range(start_port, start_port + max_attempts):
        if not is_port_in_use(p, host):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def is_dvas_edge_running(port: int) -> bool:
    """Check if the service on the port is already an active DVAS Edge session."""
    try:
        with httpx.Client(timeout=1.0) as check_client:
            resp = check_client.get(f"http://127.0.0.1:{port}/edge-launcher/health")
            if resp.status_code == 200 and resp.json().get("app") == "DVAS_Edge_Launcher":
                return True
    except Exception:
        pass
    try:
        with httpx.Client(timeout=1.0) as check_client:
            resp = check_client.get(f"http://127.0.0.1:{port}/")
            if resp.status_code == 200 and ("DVAS" in resp.text or "Drone Video Analytics" in resp.text):
                return True
    except Exception:
        pass
    return False


# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, read=None),
    verify=True,
    follow_redirects=True,
)


# ── Proxy helpers ─────────────────────────────────────────────────────────────
async def _proxy(request: Request, target: str) -> StreamingResponse:
    """Forward an HTTP request to *target* and stream the response back."""
    url = target + (f"?{request.url.query}" if request.url.query else "")
    headers = [
        (k, v) for k, v in request.headers.items()
        if k.lower() not in HOP
    ]
    upstream = await client.send(
        client.build_request(
            request.method, url,
            headers=headers,
            content=request.stream(),
        ),
        stream=True,
    )
    resp = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose),
    )
    # Forward response headers, stripping hop-by-hop and fixing cookies
    raw = []
    for k, v in upstream.headers.multi_items():
        if k.lower() in HOP:
            continue
        if k.lower() == "set-cookie":
            # Cloud cookies must work on http://localhost (strip Secure flag)
            v = v.replace("; Secure", "").replace("; secure", "")
        raw.append((k.lower().encode(), v.encode()))
    resp.raw_headers = raw
    return resp


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/edge-launcher/health")
async def launcher_health():
    """Health & single-instance check endpoint."""
    return {"status": "ok", "app": "DVAS_Edge_Launcher", "version": "1.0.0"}


@app.api_route("/edge-api/{path:path}", methods=METHODS)
async def edge_core(path: str, request: Request):
    """Proxy to the local edge core on the dynamically assigned core port."""
    target_core = getattr(request.app.state, "core_url", CORE)
    return await _proxy(request, f"{target_core}/{path}")


@app.websocket("/api/ws")
async def ws_proxy(ws: WebSocket):
    """Bidirectional WebSocket proxy to the cloud backend."""
    await ws.accept()
    target = (
        CLOUD.replace("https://", "wss://").replace("http://", "ws://")
        + "/api/ws"
    )
    if ws.url.query:
        target += "?" + ws.url.query
    try:
        async with websockets.connect(
            target, open_timeout=10, ping_interval=20
        ) as up:

            async def client_to_upstream():
                while True:
                    data = await ws.receive_text()
                    await up.send(data)

            async def upstream_to_client():
                async for msg in up:
                    if isinstance(msg, str):
                        await ws.send_text(msg)
                    else:
                        await ws.send_bytes(msg)

            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
    except Exception:
        pass  # WebSocket closed or cloud unreachable


@app.api_route("/api/{path:path}", methods=METHODS)
async def cloud_api(path: str, request: Request):
    """Proxy to the cloud backend (login, missions, audit, incidents…)."""
    return await _proxy(request, f"{CLOUD}/api/{path}")


@app.get("/{path:path}")
async def spa(path: str):
    """Serve the static React SPA; fall back to index.html for client routes."""
    f = (STATIC / path).resolve()
    if path and f.is_file() and STATIC in f.parents:
        return FileResponse(f)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


# ── Main: start edge core subprocess + gateway ───────────────────────────────
def main():
    global CORE

    preferred_ui_port   = int(os.environ.get("DVAS_PORT", os.environ.get("EDGE_GATEWAY_PORT", "8765")))
    preferred_core_port = int(os.environ.get("DVAS_CORE_PORT", os.environ.get("EDGE_CORE_PORT", "8766")))

    # 1. Single Instance Check: Is DVAS Edge already running?
    if is_port_in_use(preferred_ui_port):
        if is_dvas_edge_running(preferred_ui_port):
            print("=" * 60)
            print("  DVAS Edge Operator is already running!")
            print(f"  Focusing existing session: http://localhost:{preferred_ui_port}")
            print("=" * 60)
            webbrowser.open(f"http://localhost:{preferred_ui_port}")
            sys.exit(0)
        else:
            print(f"[!] Port {preferred_ui_port} is occupied by another application.")
            ui_port = find_free_port(preferred_ui_port + 1)
            print(f"[+] Reassigned UI Gateway to free port: {ui_port}")
    else:
        ui_port = preferred_ui_port

    # 2. Check Core Port
    if is_port_in_use(preferred_core_port) or preferred_core_port == ui_port:
        print(f"[!] Core port {preferred_core_port} is unavailable.")
        core_port = find_free_port(max(preferred_core_port + 1, ui_port + 1))
        print(f"[+] Reassigned Edge Core to free port: {core_port}")
    else:
        core_port = preferred_core_port

    CORE = f"http://127.0.0.1:{core_port}"
    app.state.core_url = CORE

    # Ensure data directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "config").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "data").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # Environment for the edge core subprocess
    env = {
        **os.environ,
        "EDGE_HOST": "127.0.0.1",
        "EDGE_PORT": str(core_port),
        "DVAS_EDGE_DATA_DIR": str(DATA_DIR / "data"),
    }

    # Start edge_api_server.py as a subprocess
    # stdin=PIPE kept open → avoids the Uvicorn EOF shutdown when no console
    core = subprocess.Popen(
        [sys.executable, str(APP_DIR / "edge_api_server.py")],
        cwd=str(APP_DIR),
        env=env,
        stdin=subprocess.PIPE,
    )

    try:
        # Auto-open the browser after a short delay
        if os.environ.get("DVAS_OPEN_BROWSER", "1") == "1":
            threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{ui_port}")).start()

        print("=" * 60)
        print("  DVAS Edge Operator")
        print(f"  UI:    http://localhost:{ui_port}")
        print(f"  Core:  {CORE}")
        print(f"  Cloud: {CLOUD}")
        print("=" * 60)

        uvicorn.run(app, host="127.0.0.1", port=ui_port, log_level="info")
    finally:
        core.terminate()
        try:
            core.wait(timeout=5)
        except subprocess.TimeoutExpired:
            core.kill()


if __name__ == "__main__":
    main()
