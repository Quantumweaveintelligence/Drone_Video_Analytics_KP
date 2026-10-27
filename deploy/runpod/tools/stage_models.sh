#!/usr/bin/env bash
# stage_models.sh — Phase 1.4 (F3): copy model weights out of the working copy into
# ../dvas_models_stage (the Docker "models" build context) and write the checksum
# manifest deploy/runpod/models.sha256 (committed; the weights are not).
# Run from the repo root:  deploy/runpod/tools/stage_models.sh [stage-dir]
# RF-DETR checkpoints (checkpoint_best_total.pth) are excluded: not used in runpod-v1.
set -euo pipefail
STAGE="${1:-../dvas_models_stage}"
[ -d dvas_analytics ] || { echo "run from the repo root" >&2; exit 2; }
mkdir -p "$STAGE/analytics"
rsync -a --delete --prune-empty-dirs --include='*/' \
  --exclude='checkpoint_best_total.pth' \
  --include='*.pt' --include='*.pth' --include='*.onnx' --include='*.pb' --exclude='*' \
  dvas_analytics/ "$STAGE/analytics/"
( cd "$STAGE" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > deploy/runpod/models.sha256
echo "staged $(wc -l < deploy/runpod/models.sha256) files, $(du -sh "$STAGE" | cut -f1) → $STAGE"
expected=(
  analytics/anpr/models/visdrone.pt analytics/anpr/models/license_plate_detector.pt analytics/anpr/models/yolov8m.pt
  analytics/crowd/models/PartAmodel_best.pth analytics/crowd/models/partBmodel_best.pth
  analytics/intrusion/models/geotrax_hbb_yolov8s_1920_v1.pt
  analytics/person/models/yolov8m.pt analytics/person/models/yolov8n-face.pt analytics/person/models/arcface_r100.onnx
  analytics/vehicle/models/visdrone.pt
  analytics/vessel/models/yolov8s.pt
  analytics/anomaly/models/weights/visdrone-yolov11x.pt analytics/anomaly/models/weights/yolo11n.pt
)
missing=0
for f in "${expected[@]}"; do [ -s "$STAGE/$f" ] || { echo "MISSING: $f"; missing=1; }; done
ls "$STAGE"/analytics/vehicle/VehicleDetectionTracker/data/*.pb >/dev/null 2>&1 || { echo "MISSING: vehicle VehicleDetectionTracker/data/*.pb"; missing=1; }
[ "$missing" -eq 0 ] && echo "all expected weights present" || { echo "some weights are missing"; exit 1; }
echo "Keep a backup copy of $STAGE (NAS / encrypted bucket) — every image build needs it."
