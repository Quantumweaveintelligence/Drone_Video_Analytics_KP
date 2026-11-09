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

Runs without a console window (pythonw.exe). Logs go to rotating files in
%LOCALAPPDATA%\\DVAS_Edge\\logs. A tray icon offers Open / Logs / Restart / Quit.
Start with --console (python.exe) to also see logs in a terminal for support.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import socket
import sys
import threading
import webbrowser

# ── Paths & imports from the edge package ─────────────────────────────────────
APP_DIR  = pathlib.Path(__file__).resolve().parent
# Edge package root: next to this file in dist/, one level up in the source tree (packaging/)
EDGE_ROOT = next((r for r in (APP_DIR, APP_DIR.parent) if (r / "edge_api_server.py").is_file()), APP_DIR)
if str(EDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(EDGE_ROOT))

import httpx
import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from src.launcher import credentials
from src.launcher.supervisor import CoreSupervisor
from src.utils.logger import build_handlers

DATA_DIR = pathlib.Path(os.environ.get("LOCALAPPDATA", str(APP_DIR))) / "DVAS_Edge"
LOG_DIR  = DATA_DIR / "logs"
CONFIG_DIR = DATA_DIR / "config"
ENV_FILE = CONFIG_DIR / "edge.env"
SECRETS_FILE = CONFIG_DIR / "secrets.dpapi"

# Load edge.env from the data directory
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

CLOUD    = os.environ.get("DVAS_CLOUD_URL", "http://localhost:3000").rstrip("/")
CORE     = "http://127.0.0.1:8766"
STATIC   = APP_DIR / "static"

log = logging.getLogger("launcher")

# Hop-by-hop headers that must NOT be forwarded
HOP = {
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "te", "trailer",
}
# Never forwarded from the browser: the core's shutdown secret belongs to this launcher only
BLOCKED_REQUEST_HEADERS = HOP | {"x-launcher-secret"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def setup_launcher_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        handlers=build_handlers(str(LOG_DIR / "launcher.log")),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        force=True,
    )


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
    except (httpx.HTTPError, ValueError) as exc:   # nothing / something else on the port
        log.debug("instance_probe_failed: %s", exc)
    try:
        with httpx.Client(timeout=1.0) as check_client:
            resp = check_client.get(f"http://127.0.0.1:{port}/")
            if resp.status_code == 200 and ("DVAS" in resp.text or "Drone Video Analytics" in resp.text):
                return True
    except (httpx.HTTPError, ValueError) as exc:   # nothing / something else on the port
        log.debug("instance_probe_failed: %s", exc)
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
        if k.lower() not in BLOCKED_REQUEST_HEADERS
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
    supervisor = getattr(app.state, "supervisor", None)
    return {
        "status": "ok",
        "app": "DVAS_Edge_Launcher",
        "version": "2.0.0",
        "core": supervisor.state if supervisor else "unknown",
    }


@app.api_route("/edge-api/{path:path}", methods=METHODS)
async def edge_core(path: str, request: Request):
    """Proxy to the local edge core on the dynamically assigned core port."""
    if path.lstrip("/").lower().startswith("internal"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    target_core = getattr(request.app.state, "core_url", CORE)
    try:
        return await _proxy(request, f"{target_core}/{path}")
    except httpx.ConnectError:
        return JSONResponse({"detail": "Edge core is starting or unavailable"}, status_code=503)


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
    except Exception as exc:
        log.info("cloud_ws_closed: %s", type(exc).__name__)


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


# ── Tray icon ─────────────────────────────────────────────────────────────────
def _open_folder(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))  # noqa: S606 - opens Explorer on our own log folder


def run_tray(ui_url: str, supervisor: CoreSupervisor, server: uvicorn.Server) -> bool:
    """Blocking tray loop on the main thread. Returns False when no tray is available."""
    try:
        import pystray
        from PIL import Image
    except ImportError as exc:
        log.warning("tray_unavailable: %s", exc)
        return False

    icon_file = STATIC / "favicon.ico"
    image = Image.open(icon_file) if icon_file.exists() else Image.new("RGB", (64, 64), (16, 64, 128))

    def quit_app(icon, _item):
        log.info("quit_requested_from_tray")
        icon.title = "DVAS Edge - stopping..."
        supervisor.stop()
        server.should_exit = True
        icon.stop()

    def restart_core(_icon, _item):
        log.info("core_restart_requested_from_tray")
        threading.Thread(target=supervisor.restart, name="CoreRestart", daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Open DVAS Edge", lambda _i, _m: webbrowser.open(ui_url), default=True),
        pystray.MenuItem("Open logs folder", lambda _i, _m: _open_folder(LOG_DIR)),
        pystray.MenuItem("Restart edge core", restart_core),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit DVAS Edge", quit_app),
    )
    icon = pystray.Icon("DVAS_Edge", image, "DVAS Edge Operator", menu)

    def on_state(state: str) -> None:
        icon.title = f"DVAS Edge Operator - core {state}"
        if state == "failed":
            icon.notify("The edge core keeps stopping. Open the logs folder and contact support.", "DVAS Edge")

    supervisor.set_state_listener(on_state)
    icon.run()
    return True


# ── Main: start edge core subprocess + gateway ───────────────────────────────
def main():
    global CORE

    setup_launcher_logging()
    console_mode = "--console" in sys.argv
    preferred_ui_port   = int(os.environ.get("DVAS_PORT", os.environ.get("EDGE_GATEWAY_PORT", "8765")))
    preferred_core_port = int(os.environ.get("DVAS_CORE_PORT", os.environ.get("EDGE_CORE_PORT", "8766")))

    # 1. Single Instance Check: Is DVAS Edge already running?
    if is_port_in_use(preferred_ui_port):
        if is_dvas_edge_running(preferred_ui_port):
            log.info("already_running; focusing http://localhost:%s", preferred_ui_port)
            webbrowser.open(f"http://localhost:{preferred_ui_port}")
            sys.exit(0)
        ui_port = find_free_port(preferred_ui_port + 1)
        log.warning("ui_port_busy port=%s; using %s", preferred_ui_port, ui_port)
    else:
        ui_port = preferred_ui_port

    # 2. Check Core Port
    if is_port_in_use(preferred_core_port) or preferred_core_port == ui_port:
        core_port = find_free_port(max(preferred_core_port + 1, ui_port + 1))
        log.warning("core_port_busy port=%s; using %s", preferred_core_port, core_port)
    else:
        core_port = preferred_core_port

    CORE = f"http://127.0.0.1:{core_port}"
    app.state.core_url = CORE

    # Ensure data directories exist
    for d in (CONFIG_DIR, DATA_DIR / "data", LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # 3. Secrets: plain text in edge.env -> DPAPI store, then into this process env
    try:
        credentials.migrate(ENV_FILE, SECRETS_FILE)
        credentials.apply(SECRETS_FILE)
    except OSError as exc:
        log.error("credential_store_unavailable: %s", exc)

    # 4. Edge core, supervised, without a console window
    supervisor = CoreSupervisor(
        cmd=[sys.executable, str(EDGE_ROOT / "edge_api_server.py")],
        cwd=EDGE_ROOT,
        env={
            **os.environ,
            "EDGE_HOST": "127.0.0.1",
            "EDGE_PORT": str(core_port),
            "DVAS_EDGE_DATA_DIR": str(DATA_DIR / "data"),
            "LOGGING__LOG_FILE": str(LOG_DIR / "core.log"),
        },
        core_url=CORE,
        stderr_log=LOG_DIR / "core-stderr.log",
    )
    app.state.supervisor = supervisor
    supervisor.start()

    ui_url = f"http://localhost:{ui_port}"
    log.info("dvas_edge_started ui=%s core=%s cloud=%s logs=%s", ui_url, CORE, CLOUD, LOG_DIR)
    if os.environ.get("DVAS_OPEN_BROWSER", "1") == "1":
        threading.Timer(2.0, lambda: webbrowser.open(ui_url)).start()

    # 5. Gateway (log_config=None: no console streams under pythonw)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ui_port, log_config=None))
    try:
        if console_mode:
            server.run()
        else:
            gateway = threading.Thread(target=server.run, name="Gateway", daemon=True)
            gateway.start()
            if not run_tray(ui_url, supervisor, server):
                gateway.join()
            gateway.join(timeout=10)
    finally:
        supervisor.stop()
        log.info("dvas_edge_stopped")


if __name__ == "__main__":
    main()
