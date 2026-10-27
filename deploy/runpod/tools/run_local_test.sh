#!/usr/bin/env bash
# run_local_test.sh — Phase 3.6: simulate the pod on the local GPU server.
# Fake network volume in $VOLDIR, port 80 → 8088. Stop the compose stacks first
# (or make sure 8088 is free).
#   deploy/runpod/tools/run_local_test.sh ghcr.io/quantumweaveintelligence/dvas-runpod:<TAG>
set -euo pipefail
IMAGE="${1:?image:tag}"
VOLDIR="${VOLDIR:-$HOME/dvas-pod-test-volume}"
mkdir -p "$VOLDIR"
docker rm -f dvas-pod-test >/dev/null 2>&1 || true
docker run -d --name dvas-pod-test --gpus all -p 8088:80 -v "$VOLDIR":/workspace \
  -e PUBLIC_BASE_URL=http://localhost:8088 \
  -e PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub 2>/dev/null || true)" \
  --shm-size=2g "$IMAGE"
echo "started; follow the first boot with:"
echo "  docker exec -it dvas-pod-test tail -f /var/log/dvas/supervisord.log /var/log/dvas/bootstrap.log"
echo "then:  docker exec -it dvas-pod-test dvas-ctl status   |   dvas-ctl health"
echo "users: $VOLDIR/dvas/secrets/users.json   (log in at http://localhost:8088)"
