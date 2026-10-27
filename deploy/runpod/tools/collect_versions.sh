#!/usr/bin/env bash
# collect_versions.sh — Phase 0 step 6: record the versions that work today in the
# running LOCAL compose stack, so Phase 3 can pin the image to them.
# Run on the Ubuntu dev/GPU server from the repo root:
#   deploy/runpod/tools/collect_versions.sh            # → deploy/runpod/versions/
set -uo pipefail
OUT="${1:-deploy/runpod/versions}"
mkdir -p "$OUT"
freeze() { # container file
  if docker inspect "$1" >/dev/null 2>&1; then
    docker exec "$1" pip freeze > "$OUT/$2" 2>/dev/null && echo "  ok   $1 → $OUT/$2" || echo "  FAIL $1"
  else echo "  skip $1 (not running)"; fi
}
echo "pip freeze:"
for svc in crowd person vehicle anpr intrusion vessel anomaly; do freeze "drone_${svc}_analyzer" "freeze_${svc}.txt"; done
freeze drone_analytics_gateway freeze_gateway.txt
freeze drone_ingest_api       freeze_ingest.txt
freeze dvas_backend           freeze_backend.txt
{
  echo "# collected $(date -Is) on $(hostname)"
  for c in drone_minio drone_keycloak drone_valkey drone_postgres dvas_postgres; do
    img="$(docker inspect --format '{{.Config.Image}} {{.Image}}' "$c" 2>/dev/null)" && echo "$c image: $img"
  done
  docker exec drone_minio minio --version 2>/dev/null | head -1 | sed 's/^/minio: /'
  docker exec drone_keycloak /opt/keycloak/bin/kc.sh --version 2>/dev/null | grep -i keycloak | head -1 | sed 's/^/keycloak: /'
  docker exec drone_valkey valkey-server --version 2>/dev/null | sed 's/^/valkey: /'
  docker exec drone_postgres postgres --version 2>/dev/null | sed 's/^/postgres(analytics): /'
  docker exec dvas_postgres postgres --version 2>/dev/null | sed 's/^/postgres(dashboard): /'
  docker exec drone_person_analyzer python -c "import torch,sys;print('analyzer python', sys.version.split()[0], 'torch', torch.__version__)" 2>/dev/null
} > "$OUT/platform_versions.txt"
echo "platform versions → $OUT/platform_versions.txt"
cat "$OUT/platform_versions.txt"
