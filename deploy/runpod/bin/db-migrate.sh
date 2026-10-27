#!/usr/bin/env bash
# db-migrate.sh [--mark-only] <database> <namespace> <file.sql> [...]
#
# Applies SQL migration files to <database> exactly once each, in the order
# given, recording them in table deploy_migrations(name, checksum, applied_at).
# name = "<namespace>/<basename>", so the analytics and dashboard migration sets
# can't collide. Needs a running Postgres reachable through the unix socket and
# must run as root (uses `runuser -u postgres`).
#
#  * CREATE EXTENSION lines are executed first as the superuser (extensions need it).
#  * The file itself runs as role "dvas" (SET ROLE dvas), so every object is owned
#    by the application role the services connect as.
#  * --mark-only records the files without executing them (used after loading a
#    baseline schema dump that already contains them).
#  * A file whose content changed after it was applied is reported, not re-run.
set -euo pipefail

MARK_ONLY=0
if [ "${1:-}" = "--mark-only" ]; then MARK_ONLY=1; shift; fi
DB="${1:?database}"; NS="${2:?namespace}"; shift 2
APP_ROLE="${DVAS_APP_ROLE:-dvas}"
PGSOCK="${PGHOST:-/run/postgresql}"

psql_su() { runuser -u postgres -- psql -X -h "$PGSOCK" -v ON_ERROR_STOP=1 -q -d "$DB" "$@"; }
log() { echo "[db-migrate] $(date -Is) $DB: $*"; }

psql_su -c "CREATE TABLE IF NOT EXISTS deploy_migrations (
              name       TEXT PRIMARY KEY,
              checksum   TEXT NOT NULL,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
            ALTER TABLE deploy_migrations OWNER TO ${APP_ROLE};" >/dev/null

applied=0; skipped=0
for f in "$@"; do
  [ -f "$f" ] || { log "missing file $f"; exit 1; }
  name="${NS}/$(basename "$f")"
  sum="$(sha256sum "$f" | cut -c1-64)"
  prev="$(psql_su -At -c "SELECT checksum FROM deploy_migrations WHERE name = '${name//\'/\'\'}'")"
  if [ -n "$prev" ]; then
    [ "$prev" = "$sum" ] || log "WARNING $name changed after it was applied — not re-run; add a new numbered migration instead"
    skipped=$((skipped + 1)); continue
  fi
  if [ "$MARK_ONLY" -eq 0 ]; then
    log "applying $name"
    exts="$(grep -iE '^[[:space:]]*CREATE[[:space:]]+EXTENSION' "$f" || true)"
    if [ -n "$exts" ]; then
      printf '%s\n' "$exts" | psql_su -f - || log "WARNING extension statement failed in $name (continuing)"
    fi
    psql_su -c "SET ROLE ${APP_ROLE}" -f "$f"
  else
    log "marking $name as applied (contained in baseline)"
  fi
  psql_su -c "INSERT INTO deploy_migrations (name, checksum) VALUES ('${name//\'/\'\'}', '${sum}')" >/dev/null
  applied=$((applied + 1))
done
log "done: $applied applied/marked, $skipped already present"
