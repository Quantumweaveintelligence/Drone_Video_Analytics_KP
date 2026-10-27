#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# dvas-init.sh — container entrypoint (PID 1 is tini). Runs on EVERY start,
# prepares the network volume, secrets, environment and (first boot only) the
# databases, then execs supervisord. RunPod plan Phase 3.5.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

DVAS_ROOT=/opt/dvas
BIN=$DVAS_ROOT/deploy/bin
VOL="${DVAS_VOLUME:-/workspace/dvas}"
PGDATA="$VOL/pgdata"
PGBIN=/usr/lib/postgresql/16/bin
log() { echo "[dvas-init] $(date -Is) $*"; }

if ! mountpoint -q /workspace 2>/dev/null; then
  log "WARNING: /workspace is not a mounted volume — databases, MinIO and secrets will NOT survive a pod stop"
fi

mkdir -p "$VOL"/{pgdata,minio,engines,backups,secrets,state,calibration} \
         /var/log/dvas /var/lib/valkey /run/postgresql /run/sshd /run/dvas
chown postgres:postgres "$PGDATA" /run/postgresql
chmod 700 "$PGDATA" "$VOL/secrets" "$VOL/backups"

# ── 1) Secrets: generated once onto the volume; real env vars win ───────────
[ -s "$VOL/secrets/dvas.env" ] && [ -s "$VOL/secrets/users.json" ] || "$BIN/gen-secrets.sh" "$VOL/secrets"
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; esac
  key="${line%%=*}"; val="${line#*=}"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  if [ -z "${!key:-}" ]; then export "$key=$val"; fi
done < "$VOL/secrets/dvas.env"

# ── 2) Runtime environment inherited by every supervisord program ───────────
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://${RUNPOD_POD_ID:-localhost}-80.proxy.runpod.net}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
export DVAS_DEPLOYMENT=cloud DVAS_RUNTIME=supervisor DVAS_VOLUME="$VOL"
case "$PUBLIC_BASE_URL" in https://*) export SECURE_COOKIES=true ;; *) export SECURE_COOKIES=false ;; esac
export ANALYTICS_DATABASE_URL="postgresql://dvas:${DVAS_DB_PASSWORD}@127.0.0.1:5432/dvas?sslmode=disable"
export DASHBOARD_DATABASE_URL="postgresql+asyncpg://dvas:${DVAS_DB_PASSWORD}@127.0.0.1:5432/dvas_dashboard"
export REDIS_URL="redis://127.0.0.1:6379/0" MINIO_ENDPOINT="127.0.0.1:9000" MINIO_SECURE=false MINIO_PUBLIC_URL=""
export KEYCLOAK_URL="http://127.0.0.1:8080" KC_REALM=kerala-police-drone KC_CLIENT_ID=drone-ingest-client
export CORS_ORIGINS="${PUBLIC_BASE_URL},http://localhost:8765" ALLOWED_ORIGINS="${PUBLIC_BASE_URL},http://localhost:8765"
export ANALYTICS_GATEWAY_URL=http://127.0.0.1:8100 \
       CROWD_ANALYTICS_URL=http://127.0.0.1:8101 PERSON_ANALYTICS_URL=http://127.0.0.1:8102 \
       VEHICLE_ANALYTICS_URL=http://127.0.0.1:8103 ANPR_ANALYTICS_URL=http://127.0.0.1:8104 \
       INTRUSION_ANALYTICS_URL=http://127.0.0.1:8105 VESSEL_ANALYTICS_URL=http://127.0.0.1:8106 \
       ANOMALY_ANALYTICS_URL=http://127.0.0.1:8107 \
       CROWD_URL=http://127.0.0.1:8101 PERSON_URL=http://127.0.0.1:8102 VEHICLE_URL=http://127.0.0.1:8103 \
       ANPR_URL=http://127.0.0.1:8104 INTRUSION_URL=http://127.0.0.1:8105 VESSEL_URL=http://127.0.0.1:8106 \
       ANOMALY_URL=http://127.0.0.1:8107
export KNOWN_DRONES="${KNOWN_DRONES:-$DRONE_IDS}"
# Tunables — override through the RunPod template environment
export VRAM_OFFLOAD_ON_STOP="${VRAM_OFFLOAD_ON_STOP:-false}"
export CROWD_MAX_FPS="${CROWD_MAX_FPS:-2}" CROWD_ALERT_THRESHOLD="${CROWD_ALERT_THRESHOLD:-50}"
export QUEUE_MAX_LENGTH="${QUEUE_MAX_LENGTH:-300}" QUEUE_MSG_TTL_MS="${QUEUE_MSG_TTL_MS:-30000}" MAX_FRAME_MB="${MAX_FRAME_MB:-5}"
export INTRUSION_DEFAULT_PRESET="${INTRUSION_DEFAULT_PRESET:-default}" REID_CLUSTER_INTERVAL="${REID_CLUSTER_INTERVAL:-50}"
export ANOMALY_CONFIDENCE="${ANOMALY_CONFIDENCE:-0.35}" ANOMALY_ALERT_HYSTERESIS_ENTER="${ANOMALY_ALERT_HYSTERESIS_ENTER:-0.72}" \
       ANOMALY_COOLDOWN_S="${ANOMALY_COOLDOWN_S:-45}" VESSEL_CONFIDENCE="${VESSEL_CONFIDENCE:-0.50}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}" TZ="${TZ:-Asia/Kolkata}" PYTHONUNBUFFERED=1

# Snapshot for interactive tools (dvas-ctl over SSH); root-only
( umask 077; export -p | grep -E 'declare -x (DVAS_|PUBLIC_BASE_URL|SECURE_COOKIES|[A-Z_]*_URL=|KC_|MINIO_|KEYCLOAK_|DRONE_IDS|KNOWN_DRONES)' > /run/dvas/runtime.env || true )

# ── 3) Compose hostnames -> localhost (catches hard-coded defaults) ─────────
for h in postgres valkey redis minio keycloak drone_nginx ingest-api backend analytics-gateway \
         crowd-analyzer person-analyzer vehicle-analyzer anpr-analyzer intrusion-analyzer \
         vessel-analyzer anomaly-analyzer; do
  grep -qE "[[:space:]]${h}([[:space:]]|\$)" /etc/hosts 2>/dev/null || echo "127.0.0.1 ${h}" >> /etc/hosts 2>/dev/null || true
done

# ── 4) SSH: key-only, key injected by RunPod as PUBLIC_KEY ──────────────────
if [ -n "${PUBLIC_KEY:-}" ]; then
  mkdir -p /root/.ssh && echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
fi
ssh-keygen -A >/dev/null 2>&1 || true
sed -i -E 's/^#?PasswordAuthentication.*/PasswordAuthentication no/; s/^#?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config || true

# ── 5) First-time database creation (Postgres on the unix socket only) ──────
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  log "initialising Postgres cluster in $PGDATA"
  runuser -u postgres -- "$PGBIN/initdb" -D "$PGDATA" -U postgres -E UTF8 --locale=C.UTF-8 \
          --auth-local=peer --auth-host=scram-sha-256 >/dev/null
fi
if [ ! -f "$VOL/state/db_bootstrapped" ]; then
  log "first boot: creating roles, databases and schema"
  runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w -t 120 \
          -o "-c config_file=/etc/dvas/postgresql.conf -c listen_addresses=''" start
  trap 'runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop || true' EXIT
  "$BIN/db-bootstrap.sh"
  runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop
  trap - EXIT
  date -Is > "$VOL/state/db_bootstrapped"
fi

# ── 6) Optional dev mode: run code from a git checkout on the volume ────────
if [ "${DVAS_DEV_MODE:-0}" = 1 ] && [ -d "$VOL/code/.git" ]; then
  log "DVAS_DEV_MODE=1: syncing Python code from $VOL/code"
  for d in dvas_analytics DVAS_Dashboard/backend dvas_ingest/ingest/api; do
    rsync -a --exclude '*.pt' --exclude '*.pth' --exclude '*.onnx' --exclude '*.pb' \
          --exclude '.env' --exclude '__pycache__' "$VOL/code/$d/" "$DVAS_ROOT/$d/"
  done
fi

log "starting supervisord (public URL: $PUBLIC_BASE_URL)"
exec /usr/bin/supervisord -c /etc/dvas/supervisord.conf
