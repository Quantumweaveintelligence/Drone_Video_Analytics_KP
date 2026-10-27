#!/usr/bin/env bash
# freeze_image_versions.sh — after the first successful build, record exactly what
# resolved in every venv so requirements/*.txt can be pinned (RunPod plan R8).
#   deploy/runpod/tools/freeze_image_versions.sh ghcr.io/quantumweaveintelligence/dvas-runpod:<TAG>
set -euo pipefail
IMAGE="${1:?image:tag}"
OUT=deploy/runpod/versions/image-freeze
mkdir -p "$OUT"
for v in ai anpr backend ingest gateway; do
  docker run --rm --entrypoint /opt/venvs/$v/bin/python "$IMAGE" -m pip freeze --local > "$OUT/$v.txt"
  echo "  $v: $(wc -l < "$OUT/$v.txt") packages → $OUT/$v.txt"
done
