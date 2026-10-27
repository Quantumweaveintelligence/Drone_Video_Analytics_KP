#!/usr/bin/env bash
# dump_baseline_schema.sh — Phase 1.3 (F2): dump the schema of the LIVE local
# databases (source of truth) into deploy/runpod/db/. Run on the dev server from
# the repo root while the compose stacks are up.
#
#   analytics_000_baseline_schema.sql   drone_postgres / dvas      (schema only)
#   analytics_001_reference_data.sql    reference rows only (presets, retention policies)
#   dashboard_000_baseline_schema.sql   dvas_postgres / dvas       (schema only)
#
# Never include detections, faces, plates or audit rows. Review the dumps before
# committing (partitions for the next months, no secrets).
set -euo pipefail
OUT=deploy/runpod/db
REF_TABLES="${REF_TABLES:-analytics_presets retention_policies}"
mkdir -p "$OUT"
docker exec drone_postgres pg_dump -U keycloak -d dvas --schema-only --no-owner --no-privileges \
  --exclude-table=deploy_migrations > "$OUT/analytics_000_baseline_schema.sql"
args=(); for t in $REF_TABLES; do args+=(-t "$t"); done
docker exec drone_postgres pg_dump -U keycloak -d dvas --data-only --no-owner --inserts "${args[@]}" \
  | sed -E 's/^INSERT INTO (.*);$/INSERT INTO \1 ON CONFLICT DO NOTHING;/' > "$OUT/analytics_001_reference_data.sql"
docker exec dvas_postgres pg_dump -U dvas -d dvas --schema-only --no-owner --no-privileges \
  --exclude-table=deploy_migrations > "$OUT/dashboard_000_baseline_schema.sql"
wc -l "$OUT"/*.sql
echo "Partitioned tables in the analytics dump:"
grep -E "PARTITION OF|PARTITION BY" "$OUT/analytics_000_baseline_schema.sql" | sed 's/^/  /' | head -40
echo "Check the table list with:  docker exec drone_postgres psql -U keycloak -d dvas -c '\\dt'"
