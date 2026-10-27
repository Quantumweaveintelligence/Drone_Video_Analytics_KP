#!/usr/bin/env bash
# phase4_preflight.sh — checks everything Phase 3 build + Phase 4 push need.
# Run from the repo root on the build machine:  BASE_IMAGE=... deploy/runpod/tools/phase4_preflight.sh
set -u
fails=0; warns=0
ok()   { printf '  OK    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fails=$((fails+1)); }
warn() { printf '  WARN  %s\n' "$1"; warns=$((warns+1)); }
MODELS="${MODELS_DIR:-../dvas_models_stage}"
DF=deploy/runpod/Dockerfile

echo "Repository"
[ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = runpod-v1 ] && ok "on branch runpod-v1" || bad "not on branch runpod-v1"
[ -z "$(git status --porcelain --untracked-files=no)" ] && ok "no uncommitted changes to tracked files" || warn "uncommitted changes — image tag would not match the commit"
if git rev-parse '@{u}' >/dev/null 2>&1; then
  [ "$(git rev-parse HEAD)" = "$(git rev-parse '@{u}')" ] && ok "HEAD pushed to $(git rev-parse --abbrev-ref '@{u}')" || warn "HEAD differs from upstream (push first)"
fi
bash deploy/runpod/tools/check_zero_byte_py.sh >/dev/null && ok "no zero-byte .py files" || bad "zero-byte .py files present"

echo "Model weights (Phase 1.4)"
if [ -s deploy/runpod/models.sha256 ] && [ -d "$MODELS/analytics" ]; then
  repo_root="$PWD"
  if ( cd "$MODELS" && sha256sum -c --quiet "$repo_root/deploy/runpod/models.sha256" ) >/dev/null 2>&1; then
    ok "$(wc -l < deploy/runpod/models.sha256) weights in $MODELS match models.sha256"
  else bad "weights in $MODELS do not match models.sha256 — re-run tools/stage_models.sh"; fi
else bad "models.sha256 or $MODELS missing — run deploy/runpod/tools/stage_models.sh"; fi

echo "Optional Phase 1 artefacts (fallbacks exist)"
[ -s deploy/runpod/db/analytics_000_baseline_schema.sql ] && ok "analytics baseline dump" || warn "no analytics baseline dump — schema will be built from migrations"
[ -s deploy/runpod/db/dashboard_000_baseline_schema.sql ] && ok "dashboard baseline dump" || warn "no dashboard baseline dump — backend create_all + audit SQL"
[ -s deploy/runpod/keycloak/realm-template.json ] && ok "realm template" || warn "no realm template — setup_keycloak.py creates the realm"

echo "Docker"
command -v docker >/dev/null && docker info >/dev/null 2>&1 && ok "docker daemon reachable" || bad "docker daemon not reachable"
docker buildx version >/dev/null 2>&1 && ok "buildx present" || bad "docker buildx missing"
root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
fstype="$(stat -f -c %T "$root" 2>/dev/null || echo unknown)"
case "$fstype" in fuseblk|ntfs*|vfat|msdos) bad "Docker root $root is on $fstype — overlay2 needs ext4/xfs";; *) ok "Docker root $root on $fstype";; esac
free_gb="$(df -BG --output=avail "$root" 2>/dev/null | tail -1 | tr -dc '0-9')"
# Budget with the classic overlay2 store + the default "docker" builder: base ~20 GB unpacked,
# our layers ~8–10 GB, node build stage + cache ~3–5 GB → ~35 GB peak.
if   [ "${free_gb:-0}" -ge 60 ]; then ok "${free_gb} GB free in $root"
elif [ "${free_gb:-0}" -ge 40 ]; then warn "${free_gb} GB free in $root — enough with overlay2 + the docker builder (~35 GB peak); don't pull/build anything else meanwhile"
else bad "only ${free_gb:-?} GB free in $root (need >= 40 GB)"; fi
storage="$(docker info --format '{{.Driver}}' 2>/dev/null)"
case "$storage" in
  overlay2) ok "image store: overlay2 (layers stored once)";;
  overlayfs|stargz|*snapshotter*) warn "image store: containerd snapshotter ($storage) keeps compressed + unpacked copies (~+10 GB) — see guide A2 to switch to overlay2";;
  *) warn "image store driver: ${storage:-unknown}";;
esac
bdriver="$(docker buildx inspect 2>/dev/null | sed -nE 's/^Driver: *//p' | head -1)"
[ "$bdriver" = docker ] && ok "buildx builder uses the docker driver (--load writes straight into the image store)"   || warn "buildx builder driver is '${bdriver:-unknown}' — run 'docker buildx use default' (other drivers export a second full copy on --load)"

echo "Base and source images can be pulled"
: "${BASE_IMAGE:=}"
[ -n "$BASE_IMAGE" ] && check_imgs=("$BASE_IMAGE") || { bad "BASE_IMAGE not set (RunPod PyTorch 2.8 template image, plan 3.1)"; check_imgs=(); }
dfarg() { local v="${!1:-}"; [ -n "$v" ] && echo "$v" || sed -nE "s/^ARG ${1}=(.*)$/\1/p" "$DF" | head -1; }
check_imgs+=("$(dfarg KEYCLOAK_IMAGE)")
minio_from="$(dfarg MINIO_FROM)"; minio_from="${minio_from:-image}"
if [ "$minio_from" = source ]; then
  check_imgs+=("$(dfarg GO_IMAGE)")
  for pair in "minio:MINIO_GIT_TAG" "mc:MC_GIT_TAG"; do
    repo="${pair%%:*}"; tag="$(dfarg "${pair#*:}")"
    if git ls-remote --exit-code --tags "https://github.com/minio/${repo}.git" "refs/tags/${tag}" >/dev/null 2>&1; then
      ok "MinIO source build: github.com/minio/${repo} tag ${tag} exists"
    else bad "MinIO source build: tag ${tag} not found in github.com/minio/${repo}"; fi
  done
else
  check_imgs+=("$(dfarg MINIO_IMAGE)" "$(dfarg MC_IMAGE)")
fi
check_imgs+=("node:20-bookworm")
for img in "${check_imgs[@]}"; do
  docker buildx imagetools inspect "$img" >/dev/null 2>&1 && ok "$img" || bad "$img not pullable (wrong tag or no access)"
done

echo "Registry"
if docker login ghcr.io </dev/null >/dev/null 2>&1; then ok "logged in to ghcr.io"
else warn "not logged in to ghcr.io — push_ghcr.sh will prompt for the PAT"; fi

echo
if [ "$fails" -eq 0 ]; then echo "Preflight passed ($warns warning(s))."; else echo "Preflight: $fails failure(s), $warns warning(s)."; exit 1; fi
