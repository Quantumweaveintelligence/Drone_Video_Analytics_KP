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
PGDATA="${PGDATA_LOCAL:-/var/lib/postgresql/dvas}"
PGBIN=/usr/lib/postgresql/16/bin
log() { echo "[dvas-init] $(date -Is) $*"; }

if ! mountpoint -q /workspace 2>/dev/null; then
  log "WARNING: /workspace is not a mounted volume — databases, MinIO and secrets will NOT survive a pod stop"
fi

mkdir -p "$VOL"/{pg,minio,engines,backups,secrets,state,calibration} \
         /var/log/dvas /var/lib/valkey /run/postgresql /run/sshd /run/dvas
chown postgres:postgres /run/postgresql
chmod 700 "$VOL/secrets" "$VOL/backups" 2>/dev/null || log "WARNING: chmod 700 on secrets/backups not permitted by the volume"

# Postgres runs on the container disk; the network volume keeps base backups + the WAL
# archive (RunPod volumes are MooseFS/FUSE: no chown, everything root:root 0777, which
# Postgres refuses). See bin/pg-volume.sh. The cluster itself is prepared in step 5.

# ── 1) Secrets: generated once onto the volume; real env vars win ───────────
"$BIN/gen-secrets.sh" "$VOL/secrets"     # idempotent: creates missing files / appends missing keys only
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
# Reports read the analytics DB through a SELECT-only role (created by dvas-bootstrap.sh)
export ANALYTICS_REPORT_DATABASE_URL="postgresql://dvas_report_ro:${DVAS_REPORT_DB_PASSWORD}@127.0.0.1:5432/dvas?sslmode=disable"
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
# GPU throughput (RunPod plan 9–10): FP16 / batch size come from the detected GPU (auto);
# the governor keeps every analyzer but crowd within DVAS_FPS_MIN–MAX, never below FLOOR.
export DVAS_FP16="${DVAS_FP16:-auto}" DVAS_MAX_BATCH="${DVAS_MAX_BATCH:-}" GOVERNOR_ENABLED="${GOVERNOR_ENABLED:-true}" \
       DVAS_FPS_MIN="${DVAS_FPS_MIN:-20}" DVAS_FPS_MAX="${DVAS_FPS_MAX:-30}" DVAS_FPS_FLOOR="${DVAS_FPS_FLOOR:-5}"
export QUEUE_WRITE_JPEG_B64="${QUEUE_WRITE_JPEG_B64:-false}" QUEUE_MEM_GUARD_PCT="${QUEUE_MEM_GUARD_PCT:-85}"
export QUEUE_MAX_LENGTH="${QUEUE_MAX_LENGTH:-2000}" QUEUE_MSG_TTL_MS="${QUEUE_MSG_TTL_MS:-30000}" MAX_FRAME_MB="${MAX_FRAME_MB:-5}"
export INTRUSION_DEFAULT_PRESET="${INTRUSION_DEFAULT_PRESET:-default}" REID_CLUSTER_INTERVAL="${REID_CLUSTER_INTERVAL:-50}"
export ANOMALY_CONFIDENCE="${ANOMALY_CONFIDENCE:-0.35}" ANOMALY_ALERT_HYSTERESIS_ENTER="${ANOMALY_ALERT_HYSTERESIS_ENTER:-0.72}" \
       ANOMALY_COOLDOWN_S="${ANOMALY_COOLDOWN_S:-45}" VESSEL_CONFIDENCE="${VESSEL_CONFIDENCE:-0.50}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}" TZ="${TZ:-Asia/Kolkata}" PYTHONUNBUFFERED=1

# CPU threads. The pod gets a cgroup CPU quota (RunPod, 30 Oct: 27.2 vCPU) but nproc shows
# the host (256 cores), so torch/OpenMP/BLAS started ~200 threads in every analyzer. Cap
# each process's compute pools to a share of the quota (override: DVAS_CPUS / OMP_NUM_THREADS).
if [ -z "${DVAS_CPUS:-}" ]; then
  # cgroup v2 (cpu.max), else v1 (cfs_quota_us / cfs_period_us; the L40S host on 30 Oct is v1)
  read -r cpu_q cpu_p 2>/dev/null < /sys/fs/cgroup/cpu.max \
    || { cpu_q="$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null)"; cpu_p="$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null)"; } \
    || cpu_q=max
  [[ "${cpu_q:-}" =~ ^[0-9]+$ && "${cpu_p:-}" =~ ^[0-9]+$ && "$cpu_p" -gt 0 ]] || cpu_q=max
  if [[ "$cpu_q" =~ ^[0-9]+$ ]]; then DVAS_CPUS=$(( (cpu_q + cpu_p - 1) / cpu_p )); else DVAS_CPUS="$(nproc)"; fi
fi
threads=$(( DVAS_CPUS / 8 )); [ "$threads" -ge 2 ] || threads=2; [ "$threads" -le 8 ] || threads=8
export DVAS_CPUS OMP_NUM_THREADS="${OMP_NUM_THREADS:-$threads}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-$OMP_NUM_THREADS}" OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-$OMP_NUM_THREADS}" \
       NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-$OMP_NUM_THREADS}"
log "CPU quota ${DVAS_CPUS} vCPU → ${OMP_NUM_THREADS} compute threads per process"

# Snapshot for interactive tools (dvas-ctl over SSH); root-only
( umask 077; export -p | grep -E 'declare -x (DVAS_|PUBLIC_BASE_URL|SECURE_COOKIES|[A-Z_]*_URL=|KC_|MINIO_|KEYCLOAK_|DRONE_IDS|KNOWN_DRONES)' > /run/dvas/runtime.env || true )

# ── 3) Compose hostnames -> localhost (catches hard-coded defaults) ─────────
for h in postgres valkey redis minio keycloak drone_nginx ingest-api backend analytics-gateway \
         crowd-analyzer person-analyzer vehicle-analyzer anpr-analyzer intrusion-analyzer \
         vessel-analyzer anomaly-analyzer; do
  grep -qE "[[:space:]]${h}([[:space:]]|\$)" /etc/hosts 2>/dev/null || echo "127.0.0.1 ${h}" >> /etc/hosts 2>/dev/null || true
done

# ── 4) SSH: key-only, key injected by RunPod as PUBLIC_KEY ──────────────────
# Extra admin keys can be kept on the volume (survive pod re-creation, no console needed):
#   /workspace/dvas/secrets/authorized_keys   (one public key per line)
mkdir -p /root/.ssh
# Only complete "<type> <base64> [comment]" lines: a key pasted wrapped over two lines (30 Oct)
# left a bare "ssh-ed25519" line and locked the builder out after every re-deploy.
{ [ -n "${PUBLIC_KEY:-}" ] && printf '%s\n' "$PUBLIC_KEY"
  [ -s "$VOL/secrets/authorized_keys" ] && cat "$VOL/secrets/authorized_keys"
} | awk 'NF >= 2 && $1 ~ /^(ssh-|ecdsa-|sk-)/ && $2 ~ /^AAAA/' | sort -u > /root/.ssh/authorized_keys || true
chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
skipped=$(grep -cvE '^[[:space:]]*(#|$)|^(ssh-|ecdsa-|sk-)[^ ]+ AAAA' "$VOL/secrets/authorized_keys" 2>/dev/null || true)
[ "${skipped:-0}" -gt 0 ] 2>/dev/null || skipped=""
log "SSH: $(wc -l < /root/.ssh/authorized_keys) authorized key(s)${skipped:+, skipped ${skipped} broken line(s) in $VOL/secrets/authorized_keys}"
ssh-keygen -A >/dev/null 2>&1 || true
sed -i -E 's/^#?PasswordAuthentication.*/PasswordAuthentication no/; s/^#?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config || true
# Don't take LANG/LC_* from SSH clients (e.g. en_IN isn't installed → perl/psql locale warnings)
sed -i -E 's/^AcceptEnv/#AcceptEnv/' /etc/ssh/sshd_config || true

# ── 5) Postgres: restore from the volume, or create (first boot only) ──────
if ! "$BIN/pg-volume.sh" prepare; then
  log "FATAL: Postgres could not be prepared (restore/init failed) — starting sshd only for inspection"
  /usr/sbin/sshd -D -e &
  exec sleep infinity
fi
[ -f /run/dvas/pg_first_boot ] && rm -f "$VOL/state/db_bootstrapped"
if [ ! -f "$VOL/state/db_bootstrapped" ]; then
  log "first boot: creating roles, databases and schema"
  runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w -t 120 \
          -o "-c config_file=/etc/dvas/postgresql.conf -c listen_addresses=''" start
  trap 'runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop || true' EXIT
  "$BIN/db-bootstrap.sh"
  "$BIN/pg-volume.sh" basebackup     # first restore point on the volume
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
