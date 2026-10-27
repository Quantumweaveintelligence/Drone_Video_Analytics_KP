#!/usr/bin/env bash
# wait-for.sh host:port [host:port ...] -- command [args...]
# Waits (up to WAIT_FOR_TIMEOUT seconds, default 300) until every TCP endpoint
# accepts connections, then execs the command. On timeout it logs and execs
# anyway: the program then fails fast and supervisord retries it.
set -u
timeout="${WAIT_FOR_TIMEOUT:-300}"
targets=()
while [ $# -gt 0 ] && [ "$1" != "--" ]; do targets+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift
if [ $# -eq 0 ]; then echo "usage: wait-for.sh host:port [...] -- command" >&2; exit 2; fi

deadline=$(( $(date +%s) + timeout ))
for t in "${targets[@]}"; do
  host="${t%:*}"; port="${t##*:}"
  until (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "[wait-for] timeout after ${timeout}s waiting for ${t}; starting anyway" >&2
      break
    fi
    sleep 1
  done
done
exec "$@"
