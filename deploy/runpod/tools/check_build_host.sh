#!/usr/bin/env bash
# check_build_host.sh — Phase 0 step 4: verify the build machine is ready.
set -u
ok=1
pass() { printf '  OK    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; ok=0; }
command -v docker >/dev/null && pass "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)" || fail "docker not installed"
docker buildx version >/dev/null 2>&1 && pass "buildx $(docker buildx version | awk '{print $2}')" || fail "docker buildx missing"
if docker info 2>/dev/null | grep -qi 'Runtimes:.*nvidia'; then pass "NVIDIA container runtime registered"
else fail "NVIDIA Container Toolkit runtime not registered (docker info | grep -i runtimes)"; fi
free_gb=$(df -BG --output=avail "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /)" | tail -1 | tr -dc '0-9')
[ "${free_gb:-0}" -ge 40 ] && pass "${free_gb} GB free for Docker (>= 40 GB needed with overlay2; 60+ comfortable)" || fail "only ${free_gb:-?} GB free for Docker (need >= 40 GB)"
command -v git >/dev/null && pass "git $(git --version | awk '{print $3}')" || fail "git missing"
if grep -q '"ghcr.io"' ~/.docker/config.json 2>/dev/null; then pass "logged in to ghcr.io"
else fail "not logged in to ghcr.io (docker login ghcr.io -u <github-user>)"; fi
[ "$ok" -eq 1 ] && echo "Build host ready." || { echo "Fix the FAIL items above."; exit 1; }
