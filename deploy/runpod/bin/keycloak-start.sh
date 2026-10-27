#!/usr/bin/env bash
# keycloak-start.sh — Keycloak 24 (optimized build baked into the image).
# DB "keycloak" on the local Postgres; HTTP on 127.0.0.1:8080 behind nginx.
# The admin console is NOT routed publicly: use an SSH tunnel
#   ssh -L 8080:127.0.0.1:8080 root@<IP> -p <PORT>   →  http://localhost:8080/admin
set -euo pipefail
: "${KC_DB_PASSWORD:?}" "${PUBLIC_BASE_URL:?}"
VOL="${DVAS_VOLUME:-/workspace/dvas}"
TEMPLATE=/opt/dvas/deploy/keycloak/realm-template.json

export KC_DB=postgres
export KC_DB_URL="jdbc:postgresql://127.0.0.1:5432/keycloak"
export KC_DB_USERNAME=keycloak
export KC_DB_PASSWORD
# KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD (from dvas.env) create the master admin on first start
export JAVA_OPTS_APPEND="${KC_JAVA_OPTS_APPEND:--Xms256m -Xmx1536m}"

# Optional realm import from the sanitised export (RunPod plan 1.5 / 2.8). When the
# template is absent, dvas-bootstrap.sh creates the realm with setup_keycloak.py.
if [ -s "$TEMPLATE" ] && [ ! -f "$VOL/state/realm_imported" ]; then
  echo "[keycloak-start] importing realm template"
  /opt/keycloak/bin/kc.sh import --optimized --file "$TEMPLATE" --override false
  date -Is > "$VOL/state/realm_imported"
fi

exec /opt/keycloak/bin/kc.sh start --optimized \
  --http-enabled=true --http-host=127.0.0.1 --http-port=8080 \
  --hostname-url="$PUBLIC_BASE_URL" \
  --hostname-admin-url="http://localhost:8080" \
  --hostname-strict=false --hostname-strict-backchannel=false \
  --proxy-headers=xforwarded
