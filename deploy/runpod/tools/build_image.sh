#!/usr/bin/env bash
# build_image.sh — Phase 3.6: build the all-in-one image (run from the repo root).
#   BASE_IMAGE=runpod/pytorch:<tag> deploy/runpod/tools/build_image.sh [--push]
# Tag = YYYYMMDD-<git short sha>; models come from ../dvas_models_stage.
# --push builds and pushes in one go (needs `docker login ghcr.io` first); the
# usual flow is: build (--load) → tools/run_local_test.sh → tools/push_ghcr.sh.
set -euo pipefail
: "${BASE_IMAGE:?set BASE_IMAGE to the RunPod PyTorch 2.8 template image (Phase 3.1)}"
MODELS="${MODELS_DIR:-../dvas_models_stage}"
REPO="${IMAGE_REPO:-ghcr.io/quantumweaveintelligence/dvas-runpod}"
[ -d "$MODELS/analytics" ] || { echo "models context $MODELS/analytics missing — run tools/stage_models.sh" >&2; exit 2; }
[ -s deploy/runpod/models.sha256 ] || { echo "deploy/runpod/models.sha256 missing — run tools/stage_models.sh" >&2; exit 2; }
bash deploy/runpod/tools/check_zero_byte_py.sh
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "WARNING: uncommitted changes — the image will not match commit $(git rev-parse --short HEAD)" >&2
fi
GIT_SHA="$(git rev-parse HEAD)"
TAG="${TAG:-$(date +%Y%m%d)-$(git rev-parse --short HEAD)}"
[ "$TAG" != latest ] || { echo "refusing tag 'latest' — use immutable tags" >&2; exit 2; }
OUTPUT="--load"; [ "${1:-}" = "--push" ] && OUTPUT="--push"
# --provenance/--sbom off: push a plain single-platform manifest (what RunPod pulls)
docker buildx build -f deploy/runpod/Dockerfile \
  --platform linux/amd64 --provenance=false --sbom=false \
  --build-context models="$MODELS" \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  --build-arg GIT_SHA="$GIT_SHA" \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  ${PY:+--build-arg PY="$PY"} \
  ${MINIO_IMAGE:+--build-arg MINIO_IMAGE="$MINIO_IMAGE"} \
  ${MC_IMAGE:+--build-arg MC_IMAGE="$MC_IMAGE"} \
  ${KEYCLOAK_IMAGE:+--build-arg KEYCLOAK_IMAGE="$KEYCLOAK_IMAGE"} \
  -t "$REPO:$TAG" $OUTPUT .
echo "built $REPO:$TAG"
[ "$OUTPUT" = "--load" ] && docker image ls "$REPO:$TAG" --format 'size: {{.Size}}'
echo "$TAG" > deploy/runpod/.last_build_tag
