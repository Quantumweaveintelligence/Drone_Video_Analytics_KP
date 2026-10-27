"""
DVAS configuration — all environment variable reads centralised here.
NOTE: .env is loaded by server.py before this module is imported.
"""
import os
import logging

_log = logging.getLogger("dvas.config")

# ---------- Deployment mode (RunPod plan Phase 2) ----------
# DVAS_DEPLOYMENT: "local" (docker-compose, default) | "cloud" (RunPod all-in-one pod)
# DVAS_RUNTIME:    "docker" (default) | "supervisor" (processes managed by supervisord)
DVAS_DEPLOYMENT = os.environ.get("DVAS_DEPLOYMENT", "local").strip().lower()
IS_CLOUD = DVAS_DEPLOYMENT == "cloud"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# ---------- Keycloak ----------
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.environ.get("KC_REALM", "kerala-police-drone")
KEYCLOAK_CLIENT_ID = os.environ.get("KC_CLIENT_ID", "drone-ingest-client")
# Secrets have NO defaults in source (RunPod plan F9). Local compose passes them
# from DVAS_Dashboard/.env; the RunPod pod generates them into dvas.env.
KEYCLOAK_CLIENT_SECRET = os.environ.get("KC_CLIENT_SECRET", "")
ADMIN_CLIENT_ID = os.environ.get("ADMIN_CLIENT_ID", "dvas-admin-service")
ADMIN_CLIENT_SECRET = os.environ.get("ADMIN_CLIENT_SECRET", "")
KEYCLOAK_ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")

# ---------- Analytics microservices ----------
ANALYTICS_BASE = os.getenv("ANALYTICS_GATEWAY_URL", "http://analytics-gateway:8100")
CROWD_URL = os.getenv("CROWD_ANALYTICS_URL", "http://crowd-analyzer:8101")
PERSON_URL = os.getenv("PERSON_ANALYTICS_URL", "http://person-analyzer:8102")
VEHICLE_URL = os.getenv("VEHICLE_ANALYTICS_URL", "http://vehicle-analyzer:8103")
ANPR_URL = os.getenv("ANPR_ANALYTICS_URL", "http://anpr-analyzer:8104")
INTRUSION_URL = os.getenv("INTRUSION_ANALYTICS_URL", "http://intrusion-analyzer:8105")
VESSEL_URL = os.getenv("VESSEL_ANALYTICS_URL", "http://vessel-analyzer:8106")
ANOMALY_URL = os.getenv("ANOMALY_ANALYTICS_URL", "http://anomaly-analyzer:8107")

# ---------- MinIO ----------
MINIO_PROXY_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_PROXY_ACCESS = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_PROXY_SECRET = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_PROXY_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# ---------- Ingest ----------
INGEST_URL = os.environ.get("INGEST_SERVER_URL", "http://drone_nginx")

# ---------- Peer DVAS Dashboard (friend's laptop command side) ----------
# URL of the peer DVAS dashboard backend (e.g. http://10.253.110.250:3000 using nginx proxy)
# Set this env var on the edge-operator side laptop to forward incidents/suggestions to the command side
DVAS_PEER_URL = os.environ.get("DVAS_PEER_URL", "").rstrip("/")
# Shared inter-service secret — must match on both laptops
# Empty => /api/push/receive rejects every request (see routers/push.py).
DVAS_SERVICE_KEY = os.environ.get("DVAS_SERVICE_KEY", "")

# ---------- CORS ----------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")


def validate_required_secrets() -> None:
    """Fail fast in cloud mode when a required secret is missing.

    In local mode a missing secret only logs an error so the compose stack can
    still start (login will fail until the value is provided).
    """
    required = {
        "KC_CLIENT_SECRET": KEYCLOAK_CLIENT_SECRET,
        "ADMIN_CLIENT_SECRET": ADMIN_CLIENT_SECRET,
    }
    if IS_CLOUD:
        required["DVAS_SERVICE_KEY"] = DVAS_SERVICE_KEY
        required["PUBLIC_BASE_URL"] = PUBLIC_BASE_URL
    missing = [name for name, value in required.items() if not value]
    if not missing:
        return
    msg = "Missing required configuration: " + ", ".join(missing)
    if IS_CLOUD:
        raise RuntimeError(msg + " (DVAS_DEPLOYMENT=cloud)")
    _log.error("%s — set them in DVAS_Dashboard/.env (local compose)", msg)

# ---------- Rate Limiter ----------
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)

