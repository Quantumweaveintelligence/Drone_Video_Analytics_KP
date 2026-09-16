"""
DVAS configuration — all environment variable reads centralised here.
NOTE: .env is loaded by server.py before this module is imported.
"""
import os

# ---------- Keycloak ----------
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.environ.get("KC_REALM", "kerala-police-drone")
KEYCLOAK_CLIENT_ID = os.environ.get("KC_CLIENT_ID", "drone-ingest-client")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KC_CLIENT_SECRET", "yNa0AfA0mToUmhQHSiM2vjAyYud6NSMn")
ADMIN_CLIENT_ID = os.environ.get("ADMIN_CLIENT_ID", "dvas-admin-service")
ADMIN_CLIENT_SECRET = os.environ.get("ADMIN_CLIENT_SECRET", "MvusAlJk9DMnfe6GVOwuni8ODQbES938")
KEYCLOAK_ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")

# ---------- Analytics microservices ----------
ANALYTICS_BASE = os.getenv("ANALYTICS_GATEWAY_URL", "http://analytics-gateway:8100")
CROWD_URL = os.getenv("CROWD_ANALYTICS_URL", "http://crowd-analyzer:8101")
PERSON_URL = os.getenv("PERSON_ANALYTICS_URL", "http://person-analyzer:8102")
VEHICLE_URL = os.getenv("VEHICLE_ANALYTICS_URL", "http://vehicle-analyzer:8103")
ANPR_URL = os.getenv("ANPR_ANALYTICS_URL", "http://anpr-analyzer:8104")
INTRUSION_URL = os.getenv("INTRUSION_ANALYTICS_URL", "http://intrusion-analyzer:8105")
VESSEL_URL = os.getenv("VESSEL_ANALYTICS_URL", "http://vessel-analyzer:8106")

# ---------- MinIO ----------
MINIO_PROXY_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_PROXY_ACCESS = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_PROXY_SECRET = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_PROXY_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# ---------- Ingest ----------
INGEST_URL = os.environ.get("INGEST_SERVER_URL", "http://drone_nginx")

# ---------- Peer DVAS Dashboard (friend's laptop command side) ----------
# URL of the peer DVAS dashboard backend (e.g. http://10.10.55.115:3000 using nginx proxy)
# Set this env var on the edge-operator side laptop to forward incidents/suggestions to the command side
DVAS_PEER_URL = os.environ.get("DVAS_PEER_URL", "").rstrip("/")
# Shared inter-service secret — must match on both laptops
DVAS_SERVICE_KEY = os.environ.get("DVAS_SERVICE_KEY", "dvas-inter-service-2026")

# ---------- CORS ----------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

# ---------- Rate Limiter ----------
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)

