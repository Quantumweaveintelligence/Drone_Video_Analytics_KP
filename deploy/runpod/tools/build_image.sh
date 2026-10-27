#!/usr/bin/env bash
# build_image.sh — Phase 3.6: build the all-in-one image (run from the repo root).
#   BASE_IMAGE=runpod/pytorch:<tag> deploy/runpod/tools/build_image.sh [--push]
# Tag = YYYYMMDD-<git short sha>; models come from ../dvas_models_stage.
set -euo pipefail
: "${BASE_IMAGE:?set BASE_IMAGE to the RunPod PyTorch 2.8 template image (Phase 3.1)}"
MODELS="${MODELS_DIR:-../dvas_models_stage}"
REPO="${IMAGE_REPO:-ghcr.io/quantumweaveintelligence/dvas-runpod}"
[ -d "$MODELS/analytics" ] || { echo "models context $MODELS/analytics missing — run tools/stage_models.sh" >&2; exit 2; }
[ -s deploy/runpod/models.sha256 ] || { echo "deploy/runpod/models.sha256 missing — run tools/stage_models.sh" >&2; exit 2; }
deploy/runpod/tools/check_zero_byte_py.sh
TAG="${TAG:-$(date +%Y%m%d)-$(git rev-parse --short HEAD)}"
OUTPUT="--load"; [ "${1:-}" = "--push" ] && OUTPUT="--push"
docker buildx build -f deploy/runpod/Dockerfile \
  --build-context models="$MODELS" \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  ${MINIO_IMAGE:+--build-arg MINIO_IMAGE="$MINIO_IMAGE"} \
  ${MC_IMAGE:+--build-arg MC_IMAGE="$MC_IMAGE"} \
  -t "$REPO:$TAG" $OUTPUT .
echo "built $REPO:$TAG"
