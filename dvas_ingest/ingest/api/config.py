"""
config.py  (Ubuntu Ingest Server — Valkey edition)
===================================================
Centralised configuration for the Kerala Police Drone Ingest API.
All values read from environment variables injected by docker-compose.

Machine:  Ubuntu 22.04 LTS Ingest Server
Hostname: ingest.dvas.internal
Queue:    Valkey (BSD-3-Clause drop-in Redis replacement)
"""
from __future__ import annotations
import os

# ── Keycloak / Auth ───────────────────────────────────────────────────────────
KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
REALM: str        = os.getenv("KC_REALM",     "kerala-police-drone")
CLIENT_ID: str    = os.getenv("KC_CLIENT_ID", "drone-ingest-client")

JWKS_URL: str  = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs"
TOKEN_URL: str = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token"

# Hostname used in Keycloak issuer URL and NGINX server_name
# Must match KC_HOSTNAME env var set on the keycloak service in docker-compose.yml
SERVER_HOSTNAME: str = os.getenv("SERVER_HOSTNAME", "ingest.dvas.internal")

# Allowed CORS origins — Windows laptops connect via the internal hostname over VPN.
# Add additional origins here if the control-room dashboard runs on a separate host.
ALLOWED_ORIGINS: list[str] = [
    f"https://{SERVER_HOSTNAME}",
    "http://localhost:3000",          # Grafana dashboard (local access)
    "http://localhost:8080",          # Keycloak admin console (local access)
    "http://10.51.27.155:3000",      # Edge laptop dashboard (LAN demo)
    "http://10.51.27.250:3000",      # Ingest server dashboard (LAN demo)
]

ALLOWED_ROLES: set[str] = {"drone-operator", "sys-admin", "control-room", "supervisor"}

# ── Valkey Queue (BSD-3-Clause drop-in Redis replacement) ────────────────────
# REDIS_URL still uses the redis:// scheme — Valkey is wire-protocol compatible.
REDIS_URL: str = os.getenv("REDIS_URL", "redis://valkey:6379/0")

# Per-drone stream settings
QUEUE_MAX_LENGTH: int = int(os.getenv("QUEUE_MAX_LENGTH", "1000"))
QUEUE_MSG_TTL_MS: int = int(os.getenv("QUEUE_MSG_TTL_MS", "30000"))

# ── Frame Validation ──────────────────────────────────────────────────────────
MAX_FRAME_BYTES: int    = int(os.getenv("MAX_FRAME_MB", "5")) * 1024 * 1024
ALLOWED_TYPES: set[str] = {"image/jpeg", "image/png"}

# ── Drone Registry ────────────────────────────────────────────────────────────
KNOWN_DRONES: list[str] = [
    d.strip()
    for d in os.getenv(
        "KNOWN_DRONES",
        "DRONE_KL_001,DRONE_KL_002,DRONE_KL_003,DRONE_KL_004,DRONE_KL_005"
    ).split(",")
    if d.strip()
]

# ── Events Stream ─────────────────────────────────────────────────────────────
EVENTS_STREAM: str = "drone:events:confirmed"

# ── API Settings ──────────────────────────────────────────────────────────────
API_TITLE: str   = "Kerala Police Drone Ingest Server"
API_VERSION: str = "1.0.0"
API_DESC: str    = (
    "Ubuntu ingest server for Kerala Police Drone Video Analytics System. "
    "Receives authenticated JPEG/PNG frames from Windows edge laptops over WireGuard VPN, "
    "validates JWT (Keycloak), and publishes to per-drone Valkey streams for GPU analytics."
)

JWKS_CACHE_TTL_S: int = int(os.getenv("JWKS_CACHE_TTL_S", "300"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str    = os.getenv("LOG_LEVEL", "INFO")
LOG_JSON: bool    = os.getenv("LOG_JSON", "true").lower() == "true"
SERVICE_NAME: str = "drone-ingest-api"
