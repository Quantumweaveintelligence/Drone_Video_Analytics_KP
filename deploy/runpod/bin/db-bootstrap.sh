#!/usr/bin/env bash
# db-bootstrap.sh — first-boot database creation (called by dvas-init.sh while a
# temporary Postgres listens on the unix socket only). RunPod plan 1.3 / 3.5.
#
#   roles:     keycloak (LOGIN), dvas (LOGIN, owns dvas + dvas_dashboard), dvas_app (NOLOGIN,
#              referenced by the analytics migrations' GRANTs)
#   databases: keycloak, dvas (analytics), dvas_dashboard (dashboard backend)
#   schema:    analytics — baseline dump deploy/runpod/db/analytics_000_baseline_schema.sql
#                          when present (Phase 1.3), then every dvas_analytics/db/migrations/*.sql
#                          (all idempotent); reference data dump if present
#              dashboard — baseline dump when present; otherwise the backend creates its
#                          tables (SQLAlchemy create_all) and dvas-bootstrap.sh applies the
#                          audit SQL afterwards
set -euo pipefail
DVAS_ROOT=/opt/dvas
BIN=$DVAS_ROOT/deploy/bin
DBDIR=$DVAS_ROOT/deploy/db
: "${KC_DB_PASSWORD:?}" "${DVAS_DB_PASSWORD:?}"
log() { echo "[db-bootstrap] $(date -Is) $*"; }
psql_su() { runuser -u postgres -- psql -X -h /run/postgresql -v ON_ERROR_STOP=1 -q "$@"; }

psql_su -d postgres \
  -v kc_pw="$KC_DB_PASSWORD" -v dvas_pw="$DVAS_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE keycloak LOGIN PASSWORD %L', :'kc_pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak') \gexec
SELECT format('ALTER ROLE keycloak PASSWORD %L', :'kc_pw') \gexec
SELECT format('CREATE ROLE dvas LOGIN PASSWORD %L', :'dvas_pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dvas') \gexec
SELECT format('ALTER ROLE dvas PASSWORD %L', :'dvas_pw') \gexec
SELECT 'CREATE ROLE dvas_app NOLOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dvas_app') \gexec
SELECT 'CREATE DATABASE keycloak OWNER keycloak'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak') \gexec
SELECT 'CREATE DATABASE dvas OWNER dvas'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dvas') \gexec
SELECT 'CREATE DATABASE dvas_dashboard OWNER dvas'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dvas_dashboard') \gexec
SQL

# load_dump <db> <file>: extensions as superuser, everything else as role dvas
load_dump() {
  local db="$1" file="$2"
  log "loading $(basename "$file") into $db"
  grep -E '^CREATE EXTENSION' "$file" | psql_su -d "$db" -f - || true
  sed -E '/^(CREATE|COMMENT ON) EXTENSION/d' "$file" | psql_su -d "$db" -c 'SET ROLE dvas' -f - >/dev/null
}

# ── Analytics DB ─────────────────────────────────────────────────────────────
if [ -s "$DBDIR/analytics_000_baseline_schema.sql" ]; then
  load_dump dvas "$DBDIR/analytics_000_baseline_schema.sql"
else
  log "no analytics baseline dump — building the schema from dvas_analytics/db/migrations"
fi
# All analytics migrations are idempotent, so they are applied on top of a
# baseline as well (picks up anything added after the dump, e.g. 012 partitions).
analytics_migrations=("$DVAS_ROOT"/dvas_analytics/db/migrations/*.sql)   # glob order = lexical
"$BIN/db-migrate.sh" dvas analytics "${analytics_migrations[@]}"
if [ -s "$DBDIR/analytics_001_reference_data.sql" ]; then
  load_dump dvas "$DBDIR/analytics_001_reference_data.sql"
fi

# ── Dashboard DB ─────────────────────────────────────────────────────────────
if [ -s "$DBDIR/dashboard_000_baseline_schema.sql" ]; then
  load_dump dvas_dashboard "$DBDIR/dashboard_000_baseline_schema.sql"
  # The live schema already contains the audit migrations (immutability triggers etc.)
  "$BIN/db-migrate.sh" --mark-only dvas_dashboard dashboard \
      "$DVAS_ROOT/DVAS_Dashboard/backend/migrations/audit_v2.sql" \
      "$DVAS_ROOT/DVAS_Dashboard/backend/migrations/audit_immutability.sql" \
      "$DVAS_ROOT/DVAS_Dashboard/backend/migrations/audit_v3_legal_holds_and_compliance.sql"
else
  log "no dashboard baseline dump — the backend creates its tables on first start"
fi
log "database bootstrap complete"
