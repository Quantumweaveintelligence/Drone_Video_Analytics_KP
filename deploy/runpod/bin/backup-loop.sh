#!/usr/bin/env bash
# backup-loop.sh — nightly database backup at 02:00 IST + hourly disk guard.
# `backup-loop.sh --once` runs one backup immediately (dvas-ctl backup-now).
# RunPod plan 3.5 / 12.3 (off-pod copies are a manual step, see the runbook).
set -uo pipefail
VOL="${DVAS_VOLUME:-/workspace/dvas}"
KEEP="${BACKUP_KEEP_SETS:-3}"
DISK_WARN_PCT="${DISK_WARN_PCT:-80}"
log() { echo "[backup] $(date -Is) $*"; }

backup_once() {
  local dest ok=1
  dest="$VOL/backups/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dest" && chmod 700 "$dest"
  for db in keycloak dvas dvas_dashboard; do
    if runuser -u postgres -- pg_dump -h /run/postgresql -Fc -d "$db" > "$dest/$db.dump"; then
      log "dumped $db ($(du -h "$dest/$db.dump" | cut -f1))"
    else
      log "ERROR: pg_dump $db failed"; ok=0
    fi
  done
  cp -p "$VOL/secrets/dvas.env" "$VOL/secrets/users.json" "$dest/" 2>/dev/null || log "WARNING: secrets not copied"
  chmod 600 "$dest"/* 2>/dev/null || true
  if [ "$ok" -eq 1 ]; then
    date -Is > "$dest/COMPLETE"
    # keep the newest $KEEP complete sets
    ls -1dt "$VOL"/backups/*/ 2>/dev/null | while read -r d; do
      [ -f "$d/COMPLETE" ] && echo "$d"; done | tail -n +"$((KEEP + 1))" | xargs -r rm -rf
    log "backup complete: $dest"
    return 0
  fi
  log "backup INCOMPLETE: $dest (kept for inspection)"
  return 1
}

disk_guard() {
  local pct
  pct="$(df --output=pcent /workspace 2>/dev/null | tail -1 | tr -dc '0-9')"
  [ -n "$pct" ] || return 0
  if [ "$pct" -ge "$DISK_WARN_PCT" ]; then
    log "WARNING: /workspace is ${pct}% full (threshold ${DISK_WARN_PCT}%) — check MinIO retention and old backups"
  fi
}

if [ "${1:-}" = "--once" ]; then backup_once; exit $?; fi

log "backup loop started (nightly 02:00 Asia/Kolkata, keep $KEEP sets)"
last_day=""
while true; do
  disk_guard
  hour="$(TZ=Asia/Kolkata date +%H)"; day="$(TZ=Asia/Kolkata date +%F)"
  if [ "$hour" = "02" ] && [ "$day" != "$last_day" ]; then
    backup_once || true
    last_day="$day"
  fi
  sleep 600
done
