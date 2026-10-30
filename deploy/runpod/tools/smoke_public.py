#!/usr/bin/env python3
"""
smoke_public.py — Phase 5.4 / Phase 6 API smoke test against the PUBLIC pod URL.

Runs from any machine (stdlib only). Reads the pod's generated secrets from a local copy
(default ~/dvas-runpod-secrets/{dvas.env,users.json}, see guide B9) and never prints them.

  deploy/runpod/tools/smoke_public.py --url https://<POD_ID>-80.proxy.runpod.net
  deploy/runpod/tools/smoke_public.py --url … --write      # also creates SMOKE-* test data

Read-only checks: health, security headers, public-path exposure (Keycloak admin, master
realm, API docs, MinIO), operator token, per-role login + every dashboard GET the command /
admin pages load, token refresh, WebSocket welcome + ping, audit hash-chain verify.
--write adds: mission start → 3 frames → status → mission end on drone DRN-SMOKE, report
generation for each type (timed against the 90 s budget; Cloudflare cuts at 100 s),
audit PDF export timing.

Exit code 0 = every check passed. The backend login is rate-limited to 5/minute per IP;
one run uses 4 logins + 1 refresh.
"""
from __future__ import annotations

import argparse
import base64
import http.cookiejar
import io
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

RESULTS: list[tuple[str, bool, str]] = []
SPA_MARKER = b'<div id="root">'


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}", flush=True)
    return ok


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.token: str | None = None

    def req(self, method: str, path: str, *, data=None, headers=None, timeout=100):
        h = {"User-Agent": "dvas-smoke/1"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        body = data
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        r = urllib.request.Request(self.base + path, data=body, headers=h, method=method)
        t0 = time.monotonic()
        try:
            with self.opener.open(r, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read(), time.monotonic() - t0
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read(), time.monotonic() - t0
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            return 0, {}, str(e).encode(), time.monotonic() - t0


def load_secrets(d: str) -> tuple[dict, list]:
    env = {}
    with open(os.path.join(d, "dvas.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    with open(os.path.join(d, "users.json")) as f:
        users = json.load(f)
    return env, users


def user_for(users: list, role: str) -> dict:
    for u in users:
        if role in (u.get("roles") or [u.get("role")]):
            return u
    raise SystemExit(f"no user with Keycloak role {role!r} in users.json")


def test_jpeg() -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise SystemExit("--write needs Pillow (pip install pillow) to make a test JPEG")
    img = Image.new("RGB", (1280, 720), (40, 90, 60))
    ImageDraw.Draw(img).text((40, 40), f"DVAS SMOKE {time.strftime('%H:%M:%S')}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue()


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, ctype, data) in files.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                  f"Content-Type: {ctype}\r\n\r\n".encode())
        out.write(data + b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def ws_roundtrip(base: str, token: str) -> tuple[bool, str]:
    """Minimal RFC 6455 client: handshake, read welcome, send 'ping', read pong."""
    u = urllib.parse.urlparse(base)
    port = u.port or (443 if u.scheme == "https" else 80)
    sock = socket.create_connection((u.hostname, port), timeout=20)
    if u.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
    key = base64.b64encode(os.urandom(16)).decode()
    path = "/api/ws?token=" + urllib.parse.quote(token)
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {base}\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            break
        head += chunk
    status = head.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 101 " not in status:
        return False, status
    rest = head.split(b"\r\n\r\n", 1)[1]

    def recv_exact(n: int) -> bytes:
        nonlocal rest
        while len(rest) < n:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed")
            rest += chunk
        out, rest = rest[:n], rest[n:]
        return out

    def read_frame() -> tuple[int, bytes]:
        b1, b2 = recv_exact(2)
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", recv_exact(8))[0]
        return b1 & 0x0F, recv_exact(ln)

    def send_text(s: str) -> None:
        p, mask = s.encode(), os.urandom(4)
        sock.sendall(bytes([0x81, 0x80 | len(p)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(p)))

    op, payload = read_frame()
    if op == 8:
        return False, f"closed by server ({struct.unpack('>H', payload[:2])[0] if len(payload) >= 2 else '?'})"
    welcome = json.loads(payload)
    send_text("ping")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        op, payload = read_frame()
        if op == 1 and json.loads(payload).get("type") == "pong":
            sock.close()
            return True, f"welcome role={welcome.get('role')}, pong ok"
    sock.close()
    return False, "no pong within 15 s"


def login(base: str, user: dict) -> tuple[Client, dict | None, str]:
    c = Client(base)
    st, _, body, dt = c.req("POST", "/api/auth/login", data={"email": user["email"], "password": user["password"]})
    if st != 200:
        return c, None, f"HTTP {st} {body[:120]!r}"
    me = json.loads(body)
    c.token = me.pop("access_token", None)
    return c, me, f"{dt:.1f}s"


def get_ok(c: Client, path: str, label: str, expect=(200,), timeout=100) -> tuple[bool, bytes]:
    st, _, body, dt = c.req("GET", path, timeout=timeout)
    ok = st in expect
    check(f"{label} GET {path}", ok, f"HTTP {st}, {dt:.1f}s" + ("" if ok else f" {body[:150]!r}"))
    return ok, body


COMMAND_GETS = [  # what the command pages load (CRO / senior)
    "/api/auth/me", "/api/drones", "/api/missions", "/api/command/kpis", "/api/alerts", "/api/evidence",
    "/api/evidence/meta", "/api/exports", "/api/incidents", "/api/reports", "/api/models",
    "/api/analytics/health", "/api/analytics/summary", "/api/analytics/models/status",
    "/api/analytics/crowd/health", "/api/analytics/person/health", "/api/analytics/vehicle/health",
    "/api/analytics/anpr/health", "/api/analytics/person/watchlist",
]
ADMIN_GETS = [
    "/api/users", "/api/audit-logs?limit=20", "/api/audit-logs/summary", "/api/audit-logs/retention-policy",
    "/api/infra/health", "/api/infra/overview", "/api/ingest-server/health", "/api/ingest-server/status",
    "/api/ingest-server/containers",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("DVAS_URL"), help="https://<POD_ID>-80.proxy.runpod.net")
    ap.add_argument("--secrets-dir", default=os.path.expanduser("~/dvas-runpod-secrets"))
    ap.add_argument("--write", action="store_true", help="also create SMOKE-* test data (test volumes only)")
    a = ap.parse_args()
    if not a.url:
        ap.error("--url (or DVAS_URL) is required")
    base = a.url.rstrip("/")
    env, users = load_secrets(a.secrets_dir)
    anon = Client(base)
    print(f"DVAS smoke test → {base}  ({time.strftime('%Y-%m-%d %H:%M:%S %Z')})")

    # ── 1. Public path & exposure ────────────────────────────────────────────
    print("\n[1] public path")
    st, _, body, dt = anon.req("GET", "/healthz")
    check("GET /healthz", st == 200 and body.strip() == b"ok", f"HTTP {st}, {dt:.2f}s")
    st, hdr, body, _ = anon.req("GET", "/")
    check("GET / serves the SPA", st == 200 and SPA_MARKER in body, f"HTTP {st}")
    missing = [h for h in ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy") if not hdr.get(h)]
    check("security headers on /", not missing, "missing " + ", ".join(missing) if missing else "")
    for p in ("/admin/master/console/", "/admin/realms", "/docs", "/openapi.json", "/minio/health/live"):
        st, _, body, _ = anon.req("GET", p)
        check(f"{p} → SPA, not the service", st == 200 and SPA_MARKER in body, f"HTTP {st}")
    for p in ("/realms/master", "/api/docs", "/api/openapi.json", "/edge-api/health"):
        st, _, _, _ = anon.req("GET", p)
        check(f"{p} not exposed", st == 404, f"HTTP {st}")
    st, _, body, _ = anon.req("GET", "/realms/kerala-police-drone/.well-known/openid-configuration")
    issuer = json.loads(body).get("issuer", "") if st == 200 else ""
    check("realm issuer = public URL (frontendUrl)", issuer == f"{base}/realms/kerala-police-drone", issuer)

    # ── 2. Operator token (edge ROPC) + ingest ───────────────────────────────
    print("\n[2] edge token + ingest")
    drone = "DRN-SMOKE"
    op = user_for(users, "drone-operator")
    form = urllib.parse.urlencode({"grant_type": "password", "client_id": "drone-ingest-client",
                                   "client_secret": env["KC_CLIENT_SECRET"],
                                   "username": op["username"], "password": op["password"]}).encode()
    st, _, body, dt = anon.req("POST", "/realms/kerala-police-drone/protocol/openid-connect/token", data=form,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
    op_token = json.loads(body).get("access_token") if st == 200 else None
    check(f"operator token ({op['username']}) via drone-ingest-client", bool(op_token), f"HTTP {st}, {dt:.2f}s")
    edge = Client(base)
    edge.token = op_token
    for p in ("/ingest/status", "/ingest/streams/active", "/api/ingest-server/streams/active",
              "/api/ingest-server/telemetry"):
        st, _, _, _ = Client(base).req("GET", p)
        check(f"GET {p} without login is refused", st in (401, 403, 404), f"HTTP {st}")
    if a.write and op_token:
        mission = f"SMOKE-{time.strftime('%Y%m%d-%H%M%S')}"
        st, _, body, _ = edge.req("POST", "/ingest/mission/start", data={
            "drone_id": drone, "mission_id": mission, "mission_name": "Phase 5 smoke test",
            "location": "smoke", "latitude": 10.0, "longitude": 76.3})
        check(f"POST /ingest/mission/start {mission}", st in (200, 201), f"HTTP {st} {body[:100]!r}")
        jpg = test_jpeg()
        times = []
        for seq in range(3):
            meta = {"drone_id": drone, "mission_id": mission, "frame_sequence": seq, "change_score": 0.5,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "capture_ts": time.time(),
                    "latitude": 10.0, "longitude": 76.3, "altitude_m": 60.0}
            data, ctype = multipart({"metadata_json": json.dumps(meta)}, {"frame": ("f.jpg", "image/jpeg", jpg)})
            st, _, body, dt = edge.req("POST", "/ingest/frame", data=data, headers={"Content-Type": ctype})
            times.append(dt)
            if st != 202:
                break
        check(f"POST /ingest/frame ×3 ({len(jpg) // 1024} KB JPEG)", st == 202,
              f"HTTP {st}, round trips {', '.join(f'{t * 1000:.0f}ms' for t in times)}")
        st, _, body, _ = edge.req("POST", "/ingest/mission/end", data={"drone_id": drone, "mission_id": mission,
                                                                       "frames_sent": 3, "notes": "smoke"})
        st, _, body, _ = Client(base).req("GET", f"/api/ingest-server/frame/{drone}/latest")
        check("live frame NOT downloadable without login", st in (401, 403), f"HTTP {st}, {len(body)} bytes")
        st, _, body, _ = edge.req("POST", "/ingest/mission/end", data={"drone_id": drone, "mission_id": mission,
                                                                       "frames_sent": 3, "notes": "smoke"})
        check("POST /ingest/mission/end", st in (200, 201, 202), f"HTTP {st}")

    # ── 3. Dashboard per role ────────────────────────────────────────────────
    print("\n[3] dashboard API per role")
    sessions: dict[str, Client] = {}
    for kc_role, expect in (("control-room", "control_room_officer"), ("supervisor", "senior_officer"),
                            ("sys-admin", "system_administrator"), ("drone-operator", "drone_operator")):
        u = user_for(users, kc_role)
        c, me, info = login(base, u)
        ok = bool(me) and me.get("role") == expect
        check(f"login {u['email']} → {expect}", ok, info if not me else f"role={me.get('role')}, {info}")
        if ok:
            sessions[kc_role] = c
        if c.jar and kc_role == "control-room":
            secure = all(ck.secure for ck in c.jar if ck.name.startswith("dvas_"))
            check("auth cookies are Secure + set", secure and any(True for _ in c.jar), "")
    if "control-room" in sessions:
        cro = sessions["control-room"]
        for p in COMMAND_GETS:
            get_ok(cro, p, "CRO")
        st, _, body, _ = cro.req("POST", "/api/auth/refresh")
        check("POST /api/auth/refresh (cookie)", st == 200, f"HTTP {st}")
        try:
            ok, info = ws_roundtrip(base, cro.token)
        except Exception as e:  # noqa: BLE001 — report any socket/protocol error as a failed check
            ok, info = False, repr(e)
        check("WebSocket /api/ws welcome + ping/pong", ok, info)
        if a.write:
            st, hdr, body, _ = cro.req("GET", f"/api/ingest-server/frame/{drone}/latest")
            check(f"CRO receives the live frame of {drone}", st == 200 and body[:2] == b"\xff\xd8",
                  f"HTTP {st}, {len(body) // 1024} KB")
        st, _, _, _ = cro.req("GET", "/api/users")
        check("CRO cannot list users (admin only)", st in (401, 403), f"HTTP {st}")
    if "sys-admin" in sessions:
        adm = sessions["sys-admin"]
        if a.write:
            get_ok(adm, f"/api/ingest-server/status/{drone}", "Admin")
        for p in ADMIN_GETS:
            ok, body = get_ok(adm, p, "Admin")
            if ok and p.endswith("/containers"):
                data = json.loads(body)
                items = data.get("containers", data) if isinstance(data, dict) else data
                names = sorted(str(i.get("name", i.get("service", "?"))) for i in items) if isinstance(items, list) else []
                check("container grid lists supervisor programs", len(names) >= 10, f"{len(names)}: {', '.join(names)[:200]}")
        ok, body = get_ok(adm, "/api/audit-logs/verify", "Admin", timeout=100)
        if ok:
            v = json.loads(body)
            check("audit hash chain verifies", v.get("is_chain_valid") is True and not v.get("tampered_count"),
                  f"{v.get('verified_count')}/{v.get('total_checked')} entries")
        if a.write:  # restarts the ingest API (a few seconds without frame upload)
            st, _, body, _ = adm.req("POST", "/api/ingest-server/control", data={"action": "restart", "scope": "core"})
            res = json.loads(body) if st == 200 else {}
            touched = {r.get("container") for r in res.get("results", [])}
            check("grid restart (scope core) touches only the ingest API", st == 200 and touched == {"ingest"},
                  f"HTTP {st}, restarted={sorted(touched)}, skipped={res.get('skipped')}")
    if "supervisor" in sessions and a.write:
        sen = sessions["supervisor"]
        for rtype in ("daily_incident", "weekly_response", "drone_utilisation"):
            t0 = time.monotonic()
            st, _, body, _ = sen.req("POST", "/api/reports/generate", data={"report_type": rtype, "params": {}})
            rid = json.loads(body).get("id") if st == 200 else None
            status = "?"
            while rid and time.monotonic() - t0 < 95:
                _, _, b, _ = sen.req("GET", f"/api/reports/{rid}")
                status = json.loads(b).get("status", "?") if b else "?"
                if status not in ("generating", "pending", "queued"):
                    break
                time.sleep(2)
            dt = time.monotonic() - t0
            check(f"report {rtype} generated < 90 s", bool(rid) and status in ("ready", "completed", "done", "generated")
                  and dt < 90, f"HTTP {st}, status={status}, {dt:.1f}s")
            if rid and status not in ("generating", "failed", "error"):
                st, hdr, b, ddt = sen.req("GET", f"/api/reports/{rid}/download")
                check(f"report {rtype} download", st == 200 and len(b) > 500,
                      f"HTTP {st}, {len(b) // 1024} KB, {hdr.get('Content-Type', '') if hdr else ''}, {ddt:.1f}s")
        st, hdr, b, dt = sen.req("GET", "/api/audit-logs/export/pdf", timeout=100)
        check("audit PDF export < 90 s", st == 200 and dt < 90, f"HTTP {st}, {len(b) // 1024} KB, {dt:.1f}s")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name, _, detail in failed:
        print(f"  FAILED: {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
