#!/usr/bin/env bash
# push_ghcr.sh — Phase 4.1: push a locally built, locally tested image to GHCR and
# record it in deploy/runpod/RELEASES.md (tag, digest, commit, base image, size).
#
#   GHCR_USER=<github-username> deploy/runpod/tools/push_ghcr.sh [TAG]
#
# TAG defaults to the last tag built by tools/build_image.sh. The PAT (classic,
# write:packages) is read from $GHCR_TOKEN or prompted for — never pass it as an
# argument (it would land in shell history / ps output). Run from the repo root.
set -euo pipefail
REPO="${IMAGE_REPO:-ghcr.io/quantumweaveintelligence/dvas-runpod}"
TAG="${1:-$(cat deploy/runpod/.last_build_tag 2>/dev/null || true)}"
[ -n "$TAG" ] || { echo "usage: push_ghcr.sh <TAG>  (no previous build tag found)" >&2; exit 2; }
[ "$TAG" != latest ] || { echo "refusing tag 'latest' — deploy immutable tags only" >&2; exit 2; }
IMAGE="$REPO:$TAG"
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "$IMAGE not found locally — build it first (tools/build_image.sh)" >&2; exit 2; }

# 1) Login (skipped when already logged in to ghcr.io)
if [ "${FORCE_LOGIN:-0}" = 1 ] || ! docker login ghcr.io </dev/null >/dev/null 2>&1; then
  : "${GHCR_USER:?set GHCR_USER to your GitHub username}"
  if [ -z "${GHCR_TOKEN:-}" ]; then read -rsp "GHCR personal access token (write:packages): " GHCR_TOKEN; echo; fi
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
  unset GHCR_TOKEN
fi

# 2) Push
echo "pushing $IMAGE (first push uploads the full image — expect a long upload)"
docker push "$IMAGE"

# 3) Verify the remote manifest and record the release
remote_digest="$(docker buildx imagetools inspect "$IMAGE" \
                 | sed -nE 's/^Digest:[[:space:]]+(sha256:[0-9a-f]{64}).*/\1/p' | head -1)"
[ -n "$remote_digest" ] || { echo "ERROR: could not read the pushed image's digest from ghcr.io" >&2; exit 1; }
local_digest="$(docker image inspect "$IMAGE" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
                | grep -F "$REPO@" | head -1 | cut -d@ -f2 || true)"
if [ -n "$local_digest" ] && [ "$local_digest" != "$remote_digest" ]; then
  echo "WARNING: local repo digest $local_digest != remote $remote_digest" >&2
fi
rev="$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
base="$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "dvas.base-image"}}')"
size="$(docker image inspect "$IMAGE" --format '{{.Size}}' | awk '{printf "%.1f GB", $1/1e9}')"
REL=deploy/runpod/RELEASES.md
[ -f "$REL" ] || printf '# DVAS RunPod image releases\n\n| Pushed (UTC) | Tag | Digest | Commit | Base image | Size |\n|---|---|---|---|---|---|\n' > "$REL"
printf '| %s | `%s` | `%s` | `%s` | `%s` | %s |\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "$TAG" "$remote_digest" "${rev:0:12}" "$base" "$size" >> "$REL"
echo
echo "pushed:  $IMAGE"
echo "digest:  $remote_digest"
echo "RunPod template → Container Image:  $IMAGE"
echo "Recorded in $REL — commit it (git add $REL && git commit -m \"release $TAG\")."
