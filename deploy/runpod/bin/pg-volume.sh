#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# pg-volume.sh — PostgreSQL on the container disk, made durable on the network volume.
#
# Why: RunPod network volumes are MooseFS over FUSE (mfs#…:9421, fuseblk). Every file
# shows as root:root 0777 and chown/chmod are refused, but Postgres must own its data
# directory with mode 0700 and refuses to run as root. So the live cluster runs on
# the container disk (fast local storage), and the volume holds everything needed to
# rebuild it:
#   $VOL/pg/base/<time>/   base backups (pg_basebackup tar + WAL), newest $PG_BASE_KEEP kept
#   $VOL/pg/wal/           continuous WAL archive, gzip (archive_command)
# `run` forces a WAL segment switch every $PG_WAL_FLUSH_SECS (default 120 s) when rows were
# written, so finished segments reach the volume; on a clean stop it switches and waits
# for the archive first. (archive_timeout isn't used: it didn't fire reliably in testing.)
# On a fresh container (new pod, or pod stop → start: the container disk is reset)
# `prepare` restores the newest base backup and replays the archived WAL. At most
# ~PG_WAL_FLUSH_SECS of commits is lost after a hard kill; none after a clean stop.
#
#   pg-volume.sh prepare            (root, dvas-init.sh) local cluster ready → exit 0
#                                     touches /run/dvas/pg_first_boot when a brand-new cluster was created
#   pg-volume.sh run                (user postgres, supervisord program "postgres")
#   pg-volume.sh basebackup         (root or postgres; server running) new base backup + prune
#   pg-volume.sh archive %p %f      (archive_command, user postgres)
#   pg-volume.sh restore %f %p      (restore_command during recovery)
#   pg-volume.sh status
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
VOL="${DVAS_VOLUME:-/workspace/dvas}"
PGDATA="${PGDATA_LOCAL:-/var/lib/postgresql/dvas}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGCONF="${PGCONF:-/etc/dvas/postgresql.conf}"
PGSOCK="${PGSOCK:-/run/postgresql}"
PGVOL="$VOL/pg"
BASE="$PGVOL/base"
WAL="$PGVOL/wal"
KEEP="${PG_BASE_KEEP:-2}"
SELF="$(readlink -f "$0")"
FLUSH_SECS="${PG_WAL_FLUSH_SECS:-120}"
log() { echo "[pg-volume] $(date -Is) $*"; }
as_pg() { if [ "$(id -un)" = postgres ]; then "$@"; else runuser -u postgres -- "$@"; fi; }
psql_pg() { as_pg "$PGBIN/psql" -X -h "$PGSOCK" -U postgres -d postgres -qAt "$@"; }

latest_base() { # newest complete base backup dir, or nothing
  local d
  for d in $(ls -1d "$BASE"/*/ 2>/dev/null | sort -r); do
    [ -f "$d/COMPLETE" ] && [ -s "$d/base.tar.gz" ] && { echo "${d%/}"; return 0; }
  done
  return 0
}

cmd_archive() { # %p %f — idempotent, never overwrites a different file; stored gzip-compressed
  local src="$1" dst="$WAL/$2.gz"
  mkdir -p "$WAL"
  if [ -f "$dst" ]; then gzip -dc "$dst" | cmp -s - "$src" && exit 0; echo "archive: $dst exists with different content" >&2; exit 1; fi
  gzip -1 -c "$src" > "$dst.tmp.$$" && mv -f "$dst.tmp.$$" "$dst"
}

cmd_restore() { # %f %p — non-zero for "not in the archive" (normal end of recovery)
  if [ -f "$WAL/$1.gz" ]; then gzip -dc "$WAL/$1.gz" > "$2"
  elif [ -f "$WAL/$1" ]; then cp "$WAL/$1" "$2"
  else exit 1; fi
}

new_local_dir() {
  mkdir -p "$(dirname "$PGDATA")"
  rm -rf "$PGDATA"; mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"; chmod 700 "$PGDATA"
}

cmd_prepare() {
  mkdir -p "$BASE" "$WAL" "$PGSOCK" /run/dvas "$VOL/state"; chown postgres:postgres "$PGSOCK"
  rm -f /run/dvas/pg_first_boot
  # postgres writes base backups + WAL here. Normal disks: chown works. MooseFS: already 0777.
  chown postgres:postgres "$PGVOL" "$BASE" "$WAL" 2>/dev/null || chmod 1777 "$PGVOL" "$BASE" "$WAL" 2>/dev/null || true
  [ -d "$VOL/pgdata" ] && rmdir "$VOL/pgdata" 2>/dev/null || true   # empty leftover of the old layout
  if [ -s "$PGDATA/PG_VERSION" ]; then
    log "local cluster present in $PGDATA (container restart) — using it"
    return 0
  fi
  local b; b="$(latest_base)"
  if [ -z "$b" ]; then
    # Brand-new database. Archived WAL from an older cluster would collide with the new one.
    if [ -n "$(ls -A "$WAL" 2>/dev/null)" ]; then
      log "WARNING: WAL archive without any base backup — moving it aside to $PGVOL/wal.orphaned-$(date +%Y%m%d-%H%M%S)"
      mv "$WAL" "$PGVOL/wal.orphaned-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$WAL"
    fi
    log "no base backup on the volume — creating a new cluster in $PGDATA"
    new_local_dir
    as_pg "$PGBIN/initdb" -D "$PGDATA" -U postgres -E UTF8 --locale=C.UTF-8 \
          --auth-local=peer --auth-host=scram-sha-256 >/dev/null
    touch /run/dvas/pg_first_boot
    return 0
  fi

  log "restoring $(basename "$b") from the volume + replaying archived WAL"
  new_local_dir
  tar -xzf "$b/base.tar.gz" -C "$PGDATA"
  [ -s "$b/pg_wal.tar.gz" ] && tar -xzf "$b/pg_wal.tar.gz" -C "$PGDATA/pg_wal"
  rm -f "$PGDATA/standby.signal"; touch "$PGDATA/recovery.signal"
  chown -R postgres:postgres "$PGDATA"; chmod 700 "$PGDATA"
  as_pg "$PGBIN/pg_ctl" -D "$PGDATA" -w -t 600 -l "$PGVOL/last-restore.log" -o \
    "-c config_file=$PGCONF -c listen_addresses='' \
     -c restore_command='$SELF restore %f %p' -c recovery_target_timeline=latest" start >/dev/null || {
      log "ERROR: recovery start failed — see $PGVOL/last-restore.log"; tail -30 "$PGVOL/last-restore.log" >&2 || true; return 1; }

  for _ in $(seq 1 600); do
    [ "$(psql_pg -c 'SELECT pg_is_in_recovery()' 2>/dev/null)" = f ] && break
    sleep 1
  done
  if [ "$(psql_pg -c 'SELECT pg_is_in_recovery()' 2>/dev/null)" != f ]; then
    log "ERROR: recovery did not finish within 10 min — see $PGVOL/last-restore.log"; return 1
  fi
  log "recovered to $(psql_pg -c "SELECT pg_walfile_name(pg_current_wal_lsn())") (timeline $(psql_pg -c 'SELECT timeline_id FROM pg_control_checkpoint()'))"
  as_pg "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null
  date -Is > "$VOL/state/pg_basebackup_needed" 2>/dev/null || true   # new timeline → fresh base soon
}

cmd_basebackup() {
  local ts tmp dst start
  ts="$(date +%Y%m%d-%H%M%S)"; tmp="$BASE/.tmp-$ts"; dst="$BASE/$ts"
  as_pg mkdir -p "$tmp"
  log "base backup → $dst"
  if ! as_pg "$PGBIN/pg_basebackup" -h "$PGSOCK" -U postgres -D "$tmp" -Ft -z -X fetch -c fast -l "dvas-$ts" 2>&1; then
    log "ERROR: pg_basebackup failed"; rm -rf "$tmp"; return 1
  fi
  start="$(tar -xzOf "$tmp/base.tar.gz" backup_label 2>/dev/null | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]\{24\}\))$/\1/p')"
  echo "$start" > "$tmp/START_WAL"
  date -Is > "$tmp/COMPLETE"
  mv "$tmp" "$dst"
  log "base backup complete ($(du -sh "$dst" | cut -f1), start WAL $start)"
  # keep the newest $KEEP complete base backups; drop WAL older than the oldest kept one.
  # (An array, not `… | head -1`: under pipefail the closed pipe aborted this function, so
  #  the WAL archive was never pruned — seen on RunPod 30 Oct.)
  local d complete=()
  for d in "$BASE"/*/; do [ -f "$d/COMPLETE" ] && complete+=("${d%/}"); done   # sorted: oldest first
  if [ "${#complete[@]}" -gt "$KEEP" ]; then
    rm -rf "${complete[@]:0:${#complete[@]}-KEEP}"
    complete=("${complete[@]:${#complete[@]}-KEEP}")
  fi
  if [ "${#complete[@]}" -gt 0 ] && [ -s "${complete[0]}/START_WAL" ]; then
    "$PGBIN/pg_archivecleanup" -x .gz "$WAL" "$(cat "${complete[0]}/START_WAL")" || log "WARNING: pg_archivecleanup failed"
  fi
  rm -rf "$BASE"/.tmp-* 2>/dev/null || true
  rm -f "$VOL/state/pg_basebackup_needed" 2>/dev/null || true
}

cmd_run() { # supervisord program: postgres in the foreground, WAL flushed to the volume on stop
  "$PGBIN/postgres" -D "$PGDATA" -c config_file="$PGCONF" &
  local pid=$! seg flusher
  ( # WAL flusher: switch segments when rows were written, so they reach the volume
    q() { "$PGBIN/psql" -X -h "$PGSOCK" -U postgres -d postgres -qAt -c "$1" 2>/dev/null; }
    w="SELECT coalesce(sum(tup_inserted+tup_updated+tup_deleted),0) FROM pg_stat_database"
    until q "SELECT 1" >/dev/null; do sleep 2; done
    prev="$(q "$w")"
    while sleep "$FLUSH_SECS"; do
      cur="$(q "$w")" || continue
      [ -n "$cur" ] && [ "$cur" != "$prev" ] && q "SELECT pg_switch_wal()" >/dev/null
      prev="$cur"
    done ) &
  flusher=$!
  on_stop() {
    trap - TERM INT
    kill "$flusher" 2>/dev/null || true
    echo "[pg-volume] stop requested — archiving the current WAL segment first"
    seg="$("$PGBIN/psql" -X -h "$PGSOCK" -U postgres -d postgres -qAt -c "SELECT pg_walfile_name(pg_switch_wal())" 2>/dev/null || true)"
    if [ -n "$seg" ]; then
      for _ in $(seq 1 30); do
        [ -f "$WAL/$seg.gz" ] && break; sleep 1
      done
      [ -f "$WAL/$seg.gz" ] && echo "[pg-volume] archived $seg" || echo "[pg-volume] WARNING: $seg not archived within 30 s"
    fi
    kill -INT "$pid" 2>/dev/null || true
  }
  trap on_stop TERM INT
  while kill -0 "$pid" 2>/dev/null; do wait "$pid" || true; done
  kill "$flusher" 2>/dev/null || true
  wait "$pid" 2>/dev/null
}

cmd_status() {
  echo "local data dir : $PGDATA ($( [ -s "$PGDATA/PG_VERSION" ] && echo present || echo missing))"
  echo "base backups   : $(ls -1d "$BASE"/*/ 2>/dev/null | wc -l) in $BASE (newest: $(basename "$(latest_base)" 2>/dev/null))"
  echo "archived WAL   : $(ls -1 "$WAL" 2>/dev/null | wc -l) files (gzip), $(du -sh "$WAL" 2>/dev/null | cut -f1)"
  psql_pg -c "SELECT 'archiver: archived=' || archived_count || ' last=' || coalesce(last_archived_wal,'-') || ' failed=' || failed_count || ' last_failed=' || coalesce(last_failed_wal,'-') FROM pg_stat_archiver" 2>/dev/null || echo "archiver: (server not running)"
}

case "${1:-}" in
  prepare)    cmd_prepare ;;
  run)        cmd_run ;;
  basebackup) cmd_basebackup ;;
  archive)    shift; cmd_archive "$@" ;;
  restore)    shift; cmd_restore "$@" ;;
  status)     cmd_status ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
