#!/usr/bin/env bash
# resolve_minio_images.sh — decide how the image gets its MinIO server + mc binaries
# and record the decision in deploy/runpod/Dockerfile (with --write).
#
#   deploy/runpod/tools/resolve_minio_images.sh             # show the decision only
#   deploy/runpod/tools/resolve_minio_images.sh --write     # write it into the Dockerfile
#   deploy/runpod/tools/resolve_minio_images.sh --source --write   # force the source build
#
# 1. "image" mode: the first pullable server/client image is pinned by digest
#    ("repo:tag@sha256:…"). Tried in order: $MINIO_IMAGE/$MC_IMAGE, the Dockerfile
#    default, the newest release tags (Docker Hub, quay.io), then :latest.
# 2. "source" mode (used when no image can be pulled — MinIO stopped publishing
#    community images in 2025): the newest RELEASE.* git tags of github.com/minio/minio
#    and github.com/minio/mc are compiled in the build with $GO_IMAGE (golang:1-bookworm).
# Run from the repo root. Needs: docker buildx, git, network.
set -euo pipefail
DF=deploy/runpod/Dockerfile
WRITE=0; FORCE_SOURCE=0
for a in "$@"; do case "$a" in --write) WRITE=1;; --source) FORCE_SOURCE=1;; *) echo "unknown option $a" >&2; exit 2;; esac; done
[ -f "$DF" ] || { echo "run from the repo root" >&2; exit 2; }

arg_default()  { sed -nE "s/^ARG $1=(.*)$/\1/p" "$DF" | head -1; }
strip_digest() { echo "${1%@*}"; }
tag_of() { local r; r="$(strip_digest "$1")"; case "${r##*/}" in *:*) echo "${r##*:}";; *) echo latest;; esac; }
set_arg() { sed -i -E "s#^ARG $1=.*#ARG $1=$2#" "$DF"; }

# Digest of an image reference (parses the plain "Digest:" line), or fail with the reason on stderr
digest_of() {
  local out d
  if ! out="$(docker buildx imagetools inspect "$1" 2>&1)"; then
    echo "        reason: $(printf '%s\n' "$out" | grep -v '^\s*$' | tail -1 | cut -c1-160)" >&2
    return 1
  fi
  d="$(printf '%s\n' "$out" | sed -nE 's/^Digest:[[:space:]]+(sha256:[0-9a-f]{64}).*/\1/p' | head -1)"
  [ -n "$d" ] && echo "$d"
}

newest_tags() { # repo-url count → newest RELEASE.* tags, newest first
  git ls-remote --tags --refs "$1" 'RELEASE.*' 2>/dev/null | awk '{print $2}' | sed 's#refs/tags/##' \
    | grep -E '^RELEASE\.[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z$' | sort -r | head -n "$2"
}

echo "Looking up MinIO release tags on GitHub…" >&2
mapfile -t SRV_TAGS < <(newest_tags https://github.com/minio/minio.git 3)
mapfile -t MC_TAGS  < <(newest_tags https://github.com/minio/mc.git 3)
[ ${#SRV_TAGS[@]} -gt 0 ] && echo "  newest minio: ${SRV_TAGS[*]}" >&2 || echo "  WARN: could not list github.com/minio/minio tags" >&2
[ ${#MC_TAGS[@]}  -gt 0 ] && echo "  newest mc:    ${MC_TAGS[*]}" >&2  || echo "  WARN: could not list github.com/minio/mc tags" >&2

resolve_image() { # label hub-repo quay-repo dockerfile-default override tag...
  local label="$1" hub="$2" quay="$3" def="$4" override="$5"; shift 5
  local cands=("$override" "$(strip_digest "$def")" "$quay:$(tag_of "$def")") t c d
  for t in "$@"; do cands+=("$hub:$t" "$quay:$t"); done
  cands+=("$hub:latest" "$quay:latest")
  for c in "${cands[@]}"; do
    [ -n "$c" ] || continue
    case "$c" in golang:*) continue;; esac       # placeholder left by source mode
    if d="$(digest_of "$c")" && [ -n "$d" ]; then
      echo "  OK    $label: $c" >&2; echo "$(strip_digest "$c")@$d"; return 0
    fi
    echo "  skip  $label: $c" >&2
  done
  return 1
}

MODE=""; SRV=""; MC=""
if [ "$FORCE_SOURCE" -eq 0 ]; then
  echo "Trying MinIO images…" >&2
  if SRV="$(resolve_image "minio server" minio/minio quay.io/minio/minio "$(arg_default MINIO_IMAGE)" "${MINIO_IMAGE:-}" "${SRV_TAGS[@]}")" \
     && MC="$(resolve_image "minio client" minio/mc quay.io/minio/mc "$(arg_default MC_IMAGE)" "${MC_IMAGE:-}" "${MC_TAGS[@]}")"; then
    MODE=image
  fi
fi

if [ -z "$MODE" ]; then
  echo "No usable MinIO image pair → building MinIO + mc from source in the Docker build." >&2
  GO_IMG="${GO_IMAGE:-$(arg_default GO_IMAGE)}"; GO_IMG="${GO_IMG:-golang:1-bookworm}"
  [ ${#SRV_TAGS[@]} -gt 0 ] && [ ${#MC_TAGS[@]} -gt 0 ] \
    || { echo "FAIL: cannot list MinIO release tags on GitHub (git ls-remote) — check internet access and send this output to Claude" >&2; exit 1; }
  digest_of "$GO_IMG" >/dev/null || { echo "FAIL: Go build image $GO_IMG is not pullable — send this output to Claude" >&2; exit 1; }
  MODE=source
fi

echo >&2
if [ "$MODE" = image ]; then
  echo "MINIO_FROM=image"
  echo "MINIO_IMAGE=$SRV"
  echo "MC_IMAGE=$MC"
else
  echo "MINIO_FROM=source"
  echo "MINIO_GIT_TAG=${SRV_TAGS[0]}"
  echo "MC_GIT_TAG=${MC_TAGS[0]}"
  echo "GO_IMAGE=$GO_IMG"
fi

if [ "$WRITE" -eq 1 ]; then
  set_arg MINIO_FROM "$MODE"
  if [ "$MODE" = image ]; then
    set_arg MINIO_IMAGE "$SRV"; set_arg MC_IMAGE "$MC"
  else
    set_arg MINIO_GIT_TAG "${SRV_TAGS[0]}"; set_arg MC_GIT_TAG "${MC_TAGS[0]}"; set_arg GO_IMAGE "$GO_IMG"
    # point the unused image ARGs at something pullable, so no stage references a dead tag
    set_arg MINIO_IMAGE "$GO_IMG"; set_arg MC_IMAGE "$GO_IMG"
  fi
  echo >&2
  echo "Written to $DF:" >&2
  grep -nE '^ARG (MINIO_FROM|MINIO_IMAGE|MC_IMAGE|MINIO_GIT_TAG|MC_GIT_TAG|GO_IMAGE)=' "$DF" >&2
  echo "Commit it:  git add $DF && git commit -m 'runpod-v1: MinIO via $MODE' && git push" >&2
else
  echo >&2; echo "Nothing changed. Re-run with --write to record this in $DF." >&2
fi
