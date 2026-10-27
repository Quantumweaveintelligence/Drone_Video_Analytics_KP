#!/usr/bin/env bash
# export_realm.sh — Phase 1.5: export realm kerala-police-drone from the LOCAL
# Keycloak and write the sanitised template deploy/runpod/keycloak/realm-template.json.
# The raw export (contains client secrets + password hashes) goes OUTSIDE git:
#   ../dvas_secrets_local/kerala-police-drone-realm.json
set -euo pipefail
RAW_DIR="${1:-../dvas_secrets_local}"
mkdir -p "$RAW_DIR"; chmod 700 "$RAW_DIR"
docker exec drone_keycloak /opt/keycloak/bin/kc.sh export --dir /tmp/kc --realm kerala-police-drone --users realm_file
docker cp drone_keycloak:/tmp/kc/kerala-police-drone-realm.json "$RAW_DIR/kerala-police-drone-realm.json"
docker exec drone_keycloak rm -rf /tmp/kc
chmod 600 "$RAW_DIR/kerala-police-drone-realm.json"
python3 deploy/runpod/tools/sanitize_realm.py "$RAW_DIR/kerala-police-drone-realm.json" deploy/runpod/keycloak/realm-template.json
