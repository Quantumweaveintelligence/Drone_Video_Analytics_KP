#!/usr/bin/env bash
# docker_disk_report.sh — show where the disk went before (re)building, and save it for Claude.
#   deploy/runpod/tools/docker_disk_report.sh        (run from the repo root; asks for sudo once for folder sizes)
# Read-only: it deletes nothing. Output → screen + deploy/runpod/.logs/disk-report-<time>.txt (git-ignored).
set -uo pipefail
[ -f deploy/runpod/Dockerfile ] || { echo "run from the repo root" >&2; exit 2; }
mkdir -p deploy/runpod/.logs
OUT="deploy/runpod/.logs/disk-report-$(date +%Y%m%d-%H%M%S).txt"

sec() { printf '\n=== %s ===\n' "$*"; }
{
  echo "DVAS disk report — $(date '+%Y-%m-%d %H:%M:%S %Z') on $(hostname)"

  sec "Free space"
  df -h / /mnt/NewVolume 2>/dev/null

  sec "Anything still building or running?"
  pgrep -af 'build_image\.sh|docker-buildx|buildx build|docker pull|resolve_minio|phase4_preflight' | grep -v pgrep || echo "(no build/pull process running)"
  docker ps --format '{{.ID}}  {{.Image}}  {{.Status}}  {{.Names}}' 2>&1 | head -20

  sec "Docker store and builder"
  echo "image store driver : $(docker info --format '{{.Driver}}' 2>&1)   (want overlay2)"
  echo "docker root dir    : $(docker info --format '{{.DockerRootDir}}' 2>&1)"
  echo "daemon.json        : $( [ -f /etc/docker/daemon.json ] && tr -d '\n' < /etc/docker/daemon.json || echo '(none)')"
  docker buildx ls 2>&1 | head -8

  sec "docker system df"
  docker system df 2>&1

  sec "Images"
  docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedSince}}' 2>&1 | head -30

  sec "Stopped containers"
  docker ps -a --filter status=exited --filter status=created --format '{{.ID}}  {{.Image}}  {{.Status}}' 2>&1 | head -20

  sec "Folder sizes (sudo)"
  sudo du -sh /var/lib/docker /var/lib/containerd /var/cache/apt 2>/dev/null || echo "(sudo du skipped)"
  journalctl --disk-usage 2>/dev/null || true
  du -sh ~/.cache/pip ~/.cache ~/.npm ~/Downloads 2>/dev/null

  sec "Build-related variables in this terminal / ~/.bashrc"
  for v in BASE_IMAGE MINIO_IMAGE MC_IMAGE KEYCLOAK_IMAGE PY TAG MODELS_DIR; do
    printf '%-15s= %s\n' "$v" "${!v:-(unset)}"
  done
  grep -nE 'BASE_IMAGE|MINIO_IMAGE|MC_IMAGE|KEYCLOAK_IMAGE|(^| )PY=' ~/.bashrc 2>/dev/null || echo "(nothing in ~/.bashrc)"

  sec "MinIO build mode in the Dockerfile"
  grep -nE '^ARG (BASE_IMAGE|MINIO_FROM|MINIO_IMAGE|MC_IMAGE|MINIO_GIT_TAG|MC_GIT_TAG|GO_IMAGE)=' deploy/runpod/Dockerfile

  sec "Git"
  git log --oneline -3 2>&1
  git status --short --untracked-files=no 2>&1 | head -20

  sec "Last build log"
  LOG="$(ls -t deploy/runpod/.logs/build-*.log 2>/dev/null | head -1)"
  [ -z "$LOG" ] && [ -f ~/dvas-build.log ] && LOG=~/dvas-build.log
  if [ -n "$LOG" ]; then
    echo "$LOG ($(wc -l < "$LOG") lines)"
    echo "--- errors ---"; grep -nE 'ERROR|error:|failed to|no space left' "$LOG" | tail -15
    echo "--- last 40 lines ---"; tail -40 "$LOG"
  else
    echo "(no build log found)"
  fi
} 2>&1 | tee "$OUT"

echo
echo "Saved: $OUT  — tell Claude \"report saved\" and it can read it from your repo folder."
