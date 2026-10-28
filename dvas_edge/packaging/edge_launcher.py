"""
DVAS Edge launcher: static UI + local edge core + cloud API reverse proxy
=========================================================================
Binds 127.0.0.1:8765 and provides:
  /              → static edge React build (REACT_APP_DEPLOYMENT_TARGET=edge)
  /edge-api/*    → edge core on 127.0.0.1:8766 (existing edge_api_server.py)
  /api/*         → cloud backend (DVAS_CLOUD_URL)
  /api/ws (WS)   → cloud WebSocket (wss://...)

This reproduces exactly what the dashboard nginx does today on a single
origin, so the React code needs NO URL changes.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
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

@app.api_route("/edge-api/{path:path}", methods=METHODS)
async def edge_core(path: str, request: Request):
    """Proxy to the local edge core on port 8766."""
    return await _proxy(request, f"{CORE}/{path}")


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
    # Ensure data directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "config").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "data").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # Environment for the edge core subprocess
    env = {
        **os.environ,
        "EDGE_HOST": "127.0.0.1",
        "EDGE_PORT": "8766",
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
            threading.Timer(2.0, lambda: webbrowser.open("http://localhost:8765")).start()

        print("=" * 60)
        print("  DVAS Edge Operator")
        print(f"  UI:    http://localhost:8765")
        print(f"  Core:  {CORE}")
        print(f"  Cloud: {CLOUD}")
        print("=" * 60)

        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
    finally:
        core.terminate()
        try:
            core.wait(timeout=5)
        except subprocess.TimeoutExpired:
            core.kill()


if __name__ == "__main__":
    main()
