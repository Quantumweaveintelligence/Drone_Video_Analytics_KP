#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# dvas-bootstrap.sh — one-shot, idempotent platform bootstrap on EVERY boot
# (supervisord program "bootstrap"). RunPod plan 3.5 / 5.6.
#   1. wait for Keycloak, MinIO, backend
#   2. Keycloak realm/clients/roles/users + frontendUrl = current pod URL
#   3. MinIO buckets (private) + lifecycle (expiry) rules
#   4. pending SQL migrations (analytics + dashboard) + partition upkeep
#   5. write state/bootstrap_ok and print a status summary
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
DVAS_ROOT=/opt/dvas
BIN=$DVAS_ROOT/deploy/bin
VOL="${DVAS_VOLUME:-/workspace/dvas}"
log() { echo "[bootstrap] $(date -Is) $*"; }
rm -f "$VOL/state/bootstrap_ok"

wait_http() { # url timeout_s
  local url="$1" deadline=$(( $(date +%s) + ${2:-600} ))
  until curl -fsS -o /dev/null --max-time 5 "$url"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then log "ERROR: timeout waiting for $url"; return 1; fi
    sleep 3
  done
  log "ready: $url"
}

# ── 1) Dependencies ──────────────────────────────────────────────────────────
wait_http http://127.0.0.1:8080/health/ready 900
wait_http http://127.0.0.1:9000/minio/health/live 300
wait_http http://127.0.0.1:8001/api/ 600

# ── 2) Keycloak (re-applies the current pod URL after a pod is re-created) ──
log "configuring Keycloak"
/opt/venvs/backend/bin/python "$DVAS_ROOT/dvas_ingest/ingest/scripts/setup_keycloak.py" \
  --url http://127.0.0.1:8080 --no-wait \
  --users-file "$VOL/secrets/users.json" \
  --frontend-url "$PUBLIC_BASE_URL"
# (client secrets, admin password come from the environment: KC_CLIENT_SECRET,
#  ADMIN_CLIENT_SECRET, KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD)

# ── 3) MinIO: private buckets + retention (5.6 — mandatory with a 30 GB volume) ─
log "configuring MinIO buckets"
export MC_CONFIG_DIR=/root/.mc
mc alias set local http://127.0.0.1:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
declare -A EXPIRY_DAYS=(
  [dvas-heatmaps]=3
  [dvas-faces]=14 [dvas-vehicles]=14 [dvas-plates]=14
  [dvas-intrusion]=14 [dvas-anomaly]=14 [dvas-vessels-maritime]=14
  [dvas-evidence]=0     # Section 65B evidence — never auto-expired
  [dvas-watchlist]=0    # reference images — never expired
)
for b in "${!EXPIRY_DAYS[@]}"; do
  mc mb --ignore-existing "local/$b" >/dev/null
  mc anonymous set none "local/$b" >/dev/null
  days="${EXPIRY_DAYS[$b]}"
  if [ "$days" -gt 0 ]; then
    # `ilm import` replaces the whole lifecycle config → idempotent on every boot
    if ! printf '{"Rules":[{"ID":"dvas-expire-%sd","Status":"Enabled","Filter":{"Prefix":""},"Expiration":{"Days":%s}}]}' \
           "$days" "$days" | mc ilm import "local/$b" >/dev/null 2>&1; then
      # older/newer mc without `ilm import`: add the rule only if none exists yet
      if ! mc ilm rule ls "local/$b" 2>/dev/null | grep -qi "expir"; then
        mc ilm rule add --expire-days "$days" "local/$b" >/dev/null
      fi
    fi
  fi
done
log "MinIO: ${#EXPIRY_DAYS[@]} buckets private; expiry rules applied"

# ── 4) SQL migrations + partition upkeep ─────────────────────────────────────
log "applying pending SQL migrations"
analytics_migrations=("$DVAS_ROOT"/dvas_analytics/db/migrations/*.sql)   # glob order = lexical
"$BIN/db-migrate.sh" dvas analytics "${analytics_migrations[@]}"
shopt -s nullglob
extra_analytics=("$DVAS_ROOT"/deploy/db/migrations/analytics/*.sql)
if [ ${#extra_analytics[@]} -gt 0 ]; then
  "$BIN/db-migrate.sh" dvas runpod-analytics "${extra_analytics[@]}"
fi
runuser -u postgres -- psql -X -h /run/postgresql -q -d dvas \
  -c "SET ROLE dvas" -c "SELECT dvas_ensure_monthly_partitions(3)" >/dev/null

# Dashboard: the backend creates its tables (SQLAlchemy create_all) on start;
# the audit SQL (hash-chain columns, immutability triggers, legal holds) follows.
for _ in $(seq 1 60); do
  if runuser -u postgres -- psql -X -h /run/postgresql -qAt -d dvas_dashboard \
       -c "SELECT to_regclass('public.audit_logs') IS NOT NULL" | grep -q t; then break; fi
  sleep 5
done
"$BIN/db-migrate.sh" dvas_dashboard dashboard \
  "$DVAS_ROOT/DVAS_Dashboard/backend/migrations/audit_v2.sql" \
  "$DVAS_ROOT/DVAS_Dashboard/backend/migrations/audit_immutability.sql" \
  "$DVAS_ROOT/DVAS_Dashboard/backend/migrations/audit_v3_legal_holds_and_compliance.sql"
extra_dashboard=("$DVAS_ROOT"/deploy/db/migrations/dashboard/*.sql)
if [ ${#extra_dashboard[@]} -gt 0 ]; then
  "$BIN/db-migrate.sh" dvas_dashboard runpod-dashboard "${extra_dashboard[@]}"
fi

# ── 4b) Fresh base backup on the volume when needed (after a restore, or none in 24 h) ──
if [ -f "$VOL/state/pg_basebackup_needed" ] || \
   [ -z "$(find "$VOL/pg/base" -mindepth 2 -maxdepth 2 -name COMPLETE -mmin -1440 2>/dev/null)" ]; then
  "$BIN/pg-volume.sh" basebackup || log "WARNING: base backup failed — the WAL archive still protects the data"
fi

# ── 5) Done ──────────────────────────────────────────────────────────────────
date -Is > "$VOL/state/bootstrap_ok"
log "bootstrap complete — status summary:"
supervisorctl -c /etc/dvas/supervisord.conf status || true
df -h / /workspace 2>/dev/null || true
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv 2>/dev/null || true
