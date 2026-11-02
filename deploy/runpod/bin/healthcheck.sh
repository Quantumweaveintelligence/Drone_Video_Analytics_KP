#!/usr/bin/env bash
# healthcheck.sh — probes every service endpoint inside the pod. Exit 1 if any fails.
set -u
fail=0
check() { # name url — HTTP 200 and, for analyzers, no "model_loaded": false
  local code body
  body="$(curl -s -w '\n%{http_code}' --max-time 5 "$2")"
  code="${body##*$'\n'}"; body="${body%$'\n'*}"
  if [ "$code" = "200" ] && ! grep -Eq '"model_loaded": ?false' <<<"$body"; then
    printf '  %-12s OK   %s\n' "$1" "$2"
  elif [ "$code" = "200" ]; then printf '  %-12s FAIL %s (model not loaded)\n' "$1" "$2"; fail=1
  else printf '  %-12s FAIL %s (HTTP %s)\n' "$1" "$2" "$code"; fail=1; fi
}
check nginx      http://127.0.0.1:80/healthz
check ingest     http://127.0.0.1:8000/health
check backend    http://127.0.0.1:8001/api/
check keycloak   http://127.0.0.1:8080/health/ready
check minio      http://127.0.0.1:9000/minio/health/live
check gateway    http://127.0.0.1:8100/health
check crowd      http://127.0.0.1:8101/health
check person     http://127.0.0.1:8102/health
check vehicle    http://127.0.0.1:8103/health
check anpr       http://127.0.0.1:8104/health
check intrusion  http://127.0.0.1:8105/health
check vessel     http://127.0.0.1:8106/health
check anomaly    http://127.0.0.1:8107/health
if valkey-cli -h 127.0.0.1 ping 2>/dev/null | grep -q PONG; then printf '  %-12s OK   127.0.0.1:6379\n' valkey
else printf '  %-12s FAIL 127.0.0.1:6379\n' valkey; fail=1; fi
if runuser -u postgres -- pg_isready -q -h /run/postgresql; then printf '  %-12s OK   /run/postgresql\n' postgres
else printf '  %-12s FAIL /run/postgresql\n' postgres; fail=1; fi
exit $fail
