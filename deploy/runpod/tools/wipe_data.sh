#!/usr/bin/env bash
# wipe_data.sh — delete every mission's data on the pod, keep the credentials and configuration.
#
#   bash wipe_data.sh            dry run: what would be deleted / kept (changes nothing)
#   bash wipe_data.sh --yes      delete
#
# Deleted: every table of the analytics (dvas) and dashboard (dvas_dashboard) databases except
#   the KEEP lists below (missions, alerts, evidence, cases, embeddings, tracks, events, audit
#   log...); every MinIO bucket's objects except dvas-watchlist (the evidence vault's
#   governance lock is bypassed); Valkey (live state, streams — users sign in again);
#   recordings, bench reports, old database backups.
# Kept: /workspace/dvas/secrets, the Keycloak database (users, passwords, roles), dashboard
#   users / settings / model config, analytics presets, retention policies, drones, zones,
#   watchlists, models (engines/).
set -euo pipefail
YES=0; [ "${1:-}" = "--yes" ] && YES=1
VOL=/workspace/dvas
CTL=/opt/dvas/deploy/bin/dvas-ctl
SV="supervisorctl -c /etc/dvas/supervisord.conf"
set -a; . "$VOL/secrets/dvas.env"; set +a

KEEP_ANALYTICS="deploy_migrations analytics_presets retention_policies drones zones person_watchlist plate_watchlist watchlist_embeddings"
KEEP_DASHBOARD="deploy_migrations alembic_version users system_settings model_config drones audit_filter_presets"
KEEP_BUCKETS="dvas-watchlist"

psql_db() { runuser -u postgres -- psql -X -h /run/postgresql -v ON_ERROR_STOP=1 -qAt -d "$1" "${@:2}"; }

tables() {   # db keep-list → the tables to empty
  local db=$1 keep=$2 q
  q="SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ($(printf "'%s'," $keep | sed 's/,$//')) ORDER BY 1"
  psql_db "$db" -c "$q"
}

echo "== databases"
for spec in "dvas:$KEEP_ANALYTICS" "dvas_dashboard:$KEEP_DASHBOARD"; do
  db=${spec%%:*}; keep=${spec#*:}
  echo "-- $db: emptied (rows now)"
  for t in $(tables "$db" "$keep"); do printf '   %-40s %s\n' "$t" "$(psql_db "$db" -c "SELECT count(*) FROM \"$t\"")"; done
  echo "-- $db: kept: $keep"
done
echo "-- keycloak database: kept (users, passwords, roles)"

mc alias set wipe http://127.0.0.1:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
echo "== MinIO buckets"
for b in $(mc ls wipe | awk '{print $NF}' | tr -d /); do
  if [[ " $KEEP_BUCKETS " == *" $b "* ]]; then echo "   $b  (kept)"; else echo "   $b  $(du -sh "$VOL/minio/$b" 2>/dev/null | cut -f1)  → emptied"; fi
done
echo "== files: recordings $(du -sh $VOL/recordings | cut -f1), bench $(du -sh $VOL/bench 2>/dev/null | cut -f1), pg backups $(du -sh $VOL/pg | cut -f1) (rebuilt)"

if [ "$YES" != 1 ]; then echo; echo "dry run — nothing changed. Run again with --yes to delete."; exit 0; fi

echo; echo "== stopping the apps (databases, MinIO, Keycloak keep running)"
$SV stop analytics:* ingest video-decoder backend go2rtc >/dev/null || true

for spec in "dvas:$KEEP_ANALYTICS" "dvas_dashboard:$KEEP_DASHBOARD"; do
  db=${spec%%:*}; keep=${spec#*:}
  list=$(tables "$db" "$keep" | sed 's/.*/"&"/' | paste -sd,)
  # no CASCADE: if a kept table points at an emptied one, this fails and nothing is emptied
  [ -n "$list" ] && psql_db "$db" -c "TRUNCATE $list RESTART IDENTITY" && echo "   $db emptied"
done

for b in $(mc ls wipe | awk '{print $NF}' | tr -d /); do
  [[ " $KEEP_BUCKETS " == *" $b "* ]] && continue
  mc rm --recursive --force --versions --bypass "wipe/$b" >/dev/null 2>&1 || mc rm --recursive --force --versions "wipe/$b" >/dev/null || true
  echo "   $b emptied ($(mc ls --recursive --versions "wipe/$b" | wc -l) objects left)"
done

valkey-cli -h 127.0.0.1 FLUSHALL >/dev/null && echo "   valkey flushed"
rm -rf "$VOL"/recordings/* "$VOL"/bench/*

echo "== new database backup (old ones held the deleted rows)"
/opt/dvas/deploy/bin/pg-volume.sh basebackup && /opt/dvas/deploy/bin/pg-volume.sh basebackup

$SV start backend go2rtc ingest video-decoder analytics:* >/dev/null || true
$CTL restart backup >/dev/null
sleep 60; $CTL disk
