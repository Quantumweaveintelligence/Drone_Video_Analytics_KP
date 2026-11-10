# syntax=docker/dockerfile:1.7
# ════════════════════════════════════════════════════════════════════════════
# DVAS all-in-one RunPod image (RunPod plan Phase 3.2)
#
# Build from the REPO ROOT with the model weights as a named build context:
#   docker buildx build -f deploy/runpod/Dockerfile \
#     --build-context models=../dvas_models_stage \
#     --build-arg BASE_IMAGE=runpod/pytorch:<TAG_FROM_TEMPLATE> \
#     -t ghcr.io/quantumweaveintelligence/dvas-runpod:<TAG> --load .
# or simply: deploy/runpod/tools/build_image.sh
# ════════════════════════════════════════════════════════════════════════════
ARG BASE_IMAGE
ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:24.0.5
# MinIO server + client (mc). Two ways to get them — tools/resolve_minio_images.sh --write sets these:
#   MINIO_FROM=image  : copy the binaries out of MINIO_IMAGE / MC_IMAGE (pinned by digest)
#   MINIO_FROM=source : compile them from the official GitHub release tags MINIO_GIT_TAG / MC_GIT_TAG
#                       with GO_IMAGE (used because MinIO stopped publishing community images in 2025)
# In "source" mode MINIO_IMAGE/MC_IMAGE point at GO_IMAGE: BuildKit still resolves every FROM line, so they must
# always name a pullable image. Defaults below = source mode with the newest release tags (checked 27 Oct 2026).
ARG MINIO_FROM=source
ARG MINIO_IMAGE=golang:1-bookworm
ARG MC_IMAGE=golang:1-bookworm
ARG MINIO_GIT_TAG=RELEASE.2025-10-15T17-29-55Z
ARG MC_GIT_TAG=RELEASE.2025-08-13T08-35-41Z
ARG GO_IMAGE=golang:1-bookworm

# ── Stage 1: cloud frontend build ─────────────────────────────────────────────
FROM node:20-bookworm AS frontend
WORKDIR /src
COPY DVAS_Dashboard/frontend/package.json DVAS_Dashboard/frontend/package-lock.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund
COPY DVAS_Dashboard/frontend/ ./
ENV REACT_APP_DEPLOYMENT_TARGET=cloud REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false CI=false
RUN npm run build && test -f build/index.html

# ── Stage 2: Keycloak + MinIO binaries ────────────────────────────────────────
FROM ${KEYCLOAK_IMAGE} AS keycloak

# MinIO variant "image": binaries from the official images (at /usr/bin/minio, /usr/bin/mc)
FROM ${MINIO_IMAGE} AS minio-image
FROM ${MC_IMAGE}    AS mc-image

# MinIO variant "source": static binaries built from the release tags, installed at the same paths
FROM ${GO_IMAGE} AS minio-source
ARG MINIO_GIT_TAG
ENV CGO_ENABLED=0 GOTOOLCHAIN=auto
RUN set -eux; \
    git clone --depth 1 --branch "${MINIO_GIT_TAG}" https://github.com/minio/minio.git /src/minio; \
    cd /src/minio; \
    LDFLAGS="$(go run buildscripts/gen-ldflags.go 2>/dev/null || true)"; \
    go build -trimpath -tags kqueue -ldflags "-s -w ${LDFLAGS}" -o /usr/bin/minio .; \
    /usr/bin/minio --version; \
    rm -rf /src /root/go/pkg /root/.cache/go-build

FROM ${GO_IMAGE} AS mc-source
ARG MC_GIT_TAG
ENV CGO_ENABLED=0 GOTOOLCHAIN=auto
RUN set -eux; \
    git clone --depth 1 --branch "${MC_GIT_TAG}" https://github.com/minio/mc.git /src/mc; \
    cd /src/mc; \
    LDFLAGS="$(go run buildscripts/gen-ldflags.go 2>/dev/null || true)"; \
    go build -trimpath -tags kqueue -ldflags "-s -w ${LDFLAGS}" -o /usr/bin/mc .; \
    /usr/bin/mc --version; \
    rm -rf /src /root/go/pkg /root/.cache/go-build

# Select the variant
FROM minio-${MINIO_FROM} AS minio
FROM mc-${MINIO_FROM}    AS mc

# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM ${BASE_IMAGE}
# Interpreter that has torch. Empty = auto-detect from PY_CANDIDATES (works for the
# RunPod Ubuntu 22.04 / py3.11 and Ubuntu 24.04 / py3.12 PyTorch 2.8 images).
ARG PY=
ARG PY_CANDIDATES="/usr/bin/python3 /usr/local/bin/python3 /usr/bin/python3.12 /usr/bin/python3.11 /usr/local/bin/python /opt/conda/bin/python"
ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Kolkata PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Don't let the postgres package create a default cluster (ours lives on /workspace)
RUN mkdir -p /etc/postgresql-common && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf

# OS packages — must work on Ubuntu 22.04 AND 24.04 bases:
#  * PostgreSQL 16 from the distro (24.04) or from apt.postgresql.org (22.04)
#  * Java 21 when packaged, else 17 (Keycloak 24 needs >= 17)
#  * Valkey from the distro when packaged, else redis-server 7.x (same wire protocol,
#    same config). The official valkey:7.2 image is now Debian trixie (glibc 2.41), so
#    copying its binary into an Ubuntu base would fail on missing GLIBC symbols.
RUN set -eux; . /etc/os-release; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    if ! apt-cache show postgresql-16 >/dev/null 2>&1; then \
      install -d /usr/share/postgresql-common/pgdg; \
      curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
      echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
      apt-get update; \
    fi; \
    JAVA_PKG=openjdk-21-jre-headless; apt-cache show "$JAVA_PKG" >/dev/null 2>&1 || JAVA_PKG=openjdk-17-jre-headless; \
    KV_PKG=valkey-server;             apt-cache show "$KV_PKG"   >/dev/null 2>&1 || KV_PKG=redis-server; \
    apt-get install -y --no-install-recommends \
      nginx supervisor tini openssh-server jq rsync tzdata util-linux \
      postgresql-16 postgresql-client-16 "$JAVA_PKG" "$KV_PKG" \
      ffmpeg libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 libxrender1; \
    if [ "$KV_PKG" = redis-server ]; then kv_srv=redis-server; kv_cli=redis-cli; else kv_srv=valkey-server; kv_cli=valkey-cli; fi; \
    ln -sf "$(command -v "$kv_srv")" /usr/local/bin/valkey-server; \
    ln -sf "$(command -v "$kv_cli")" /usr/local/bin/valkey-cli; \
    echo "os=${PRETTY_NAME} java=${JAVA_PKG} kv=${KV_PKG}" > /opt/dvas-os-packages.txt; \
    rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

# Keycloak, MinIO, mc
COPY --from=keycloak /opt/keycloak /opt/keycloak
COPY --from=minio /usr/bin/minio /usr/local/bin/minio
COPY --from=mc /usr/bin/mc /usr/local/bin/mc
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true --metrics-enabled=false \
 && valkey-server --version && minio --version && mc --version && java -version \
 && /usr/lib/postgresql/16/bin/postgres --version && cat /opt/dvas-os-packages.txt

# Python: find the interpreter that has torch, pin torch/torchvision to it (local
# version label stripped: "2.8.0+cu128" -> "2.8.0") and make sure venv works.
RUN set -eux; PYBIN=""; \
    for p in ${PY} ${PY_CANDIDATES}; do \
      if [ -x "$p" ] && "$p" -c 'import torch, torchvision' 2>/dev/null; then PYBIN="$p"; break; fi; \
    done; \
    test -n "$PYBIN" || { echo "No interpreter with torch+torchvision in: ${PY} ${PY_CANDIDATES}" >&2; exit 1; }; \
    echo "$PYBIN" > /opt/dvas-python; "$PYBIN" --version; \
    "$PYBIN" -c "import sys;assert sys.version_info[:2]>=(3,10),sys.version"; \
    "$PYBIN" -c "import torch,torchvision;print(f'torch=={torch.__version__.split(\"+\")[0]}\ntorchvision=={torchvision.__version__.split(\"+\")[0]}')" > /opt/torch-constraints.txt; \
    cat /opt/torch-constraints.txt; \
    if ! "$PYBIN" -m venv /tmp/venvcheck >/dev/null 2>&1; then \
      v="$("$PYBIN" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"; \
      apt-get update && apt-get install -y --no-install-recommends "python${v}-venv" && rm -rf /var/lib/apt/lists/*; \
      "$PYBIN" -m venv /tmp/venvcheck; \
    fi; \
    rm -rf /tmp/venvcheck
COPY deploy/runpod/requirements/ /opt/dvas/requirements/
RUN set -e; PY="$(cat /opt/dvas-python)"; \
  for v in ai anpr; do \
    ${PY} -m venv --system-site-packages /opt/venvs/$v && \
    /opt/venvs/$v/bin/python -m pip install --upgrade pip wheel && \
    /opt/venvs/$v/bin/python -m pip install -c /opt/torch-constraints.txt -r /opt/dvas/requirements/$v.txt; \
  done; \
  for v in backend ingest gateway; do \
    ${PY} -m venv /opt/venvs/$v && \
    /opt/venvs/$v/bin/python -m pip install --upgrade pip wheel && \
    /opt/venvs/$v/bin/python -m pip install -r /opt/dvas/requirements/$v.txt; \
  done; \
  /opt/venvs/ai/bin/python -c "import torch;assert torch.__version__.startswith('2.8'), torch.__version__;print('torch', torch.__version__, 'cuda', torch.version.cuda)"; \
  /opt/venvs/ai/bin/python -c "import onnxruntime as o;print('onnxruntime providers', o.get_available_providers())"

# go2rtc (v2 R1/R2): live video for the dashboard — pinned release, SHA-256 verified
# (digest from the GitHub release asset). After the venvs so their layers stay cached.
ARG GO2RTC_VERSION=v1.9.14
ARG GO2RTC_SHA256=32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6
RUN curl -fsSL -o /usr/local/bin/go2rtc \
      "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_amd64" && \
    echo "${GO2RTC_SHA256}  /usr/local/bin/go2rtc" | sha256sum -c - && \
    chmod 755 /usr/local/bin/go2rtc && /usr/local/bin/go2rtc -version && \
    /opt/venvs/ai/bin/python -c "import av, cv2, redis; print('video-decoder deps: av', av.__version__, 'cv2', cv2.__version__)"

# yolov8n-face.pt was pickled with omegaconf objects; without the package Ultralytics pip-installs
# it at model load (internet, untracked version). Its own small layer so the venv layer above
# stays cached; move it into requirements/ai.txt at the next full venv rebuild.
RUN /opt/venvs/ai/bin/python -m pip install --no-cache-dir "omegaconf>=2.3,<2.5" && \
    /opt/venvs/ai/bin/python -c "import omegaconf; print('omegaconf', omegaconf.__version__)"

# Application code (weights excluded by Dockerfile.dockerignore)
COPY dvas_analytics/             /opt/dvas/dvas_analytics/
COPY dvas_ingest/ingest/api/     /opt/dvas/dvas_ingest/ingest/api/
COPY dvas_ingest/ingest/scripts/ /opt/dvas/dvas_ingest/ingest/scripts/
COPY DVAS_Dashboard/backend/     /opt/dvas/DVAS_Dashboard/backend/
COPY --from=frontend /src/build  /opt/dvas/frontend
COPY deploy/runpod/              /opt/dvas/deploy/

# Model weights ONLY from the verified "models" build context (../dvas_models_stage)
COPY --from=models analytics/    /opt/dvas/dvas_analytics/
RUN cd /opt/dvas && test -s deploy/models.sha256 && \
    sed 's#  \./analytics/#  ./dvas_analytics/#' deploy/models.sha256 | sha256sum -c --quiet && \
    ln -s /opt/dvas/dvas_analytics /analytics

# Pre-download PaddleOCR models (same constructor args as anpr/analyzer.py) and
# byte-compile everything with the base image's Python as a smoke test (F16).
RUN /opt/venvs/anpr/bin/python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', use_gpu=False, show_log=False)" && \
    /opt/venvs/ai/bin/python -m compileall -q /opt/dvas/dvas_analytics && \
    /opt/venvs/backend/bin/python -m compileall -q /opt/dvas/DVAS_Dashboard/backend && \
    /opt/venvs/ingest/bin/python -m compileall -q /opt/dvas/dvas_ingest/ingest/api

# Config + scripts
RUN mkdir -p /etc/dvas /var/log/dvas /var/lib/valkey /run/postgresql /run/sshd && \
    cp /opt/dvas/deploy/etc/* /etc/dvas/ && chmod +x /opt/dvas/deploy/bin/* && \
    ln -sf /opt/dvas/deploy/bin/dvas-ctl /usr/local/bin/dvas-ctl && \
    chown postgres:postgres /run/postgresql && \
    nginx -t -c /etc/dvas/nginx.conf

# Build metadata LAST: an ARG in scope is part of every later RUN's cache key, so declaring
# GIT_SHA/BUILD_DATE at the top made every commit rebuild the whole image (~20 min, ~11 GB).
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG BASE_IMAGE
LABEL org.opencontainers.image.source="https://github.com/Quantumweaveintelligence/Drone_Video_Analytics_KP" \
      org.opencontainers.image.title="dvas-runpod" \
      org.opencontainers.image.description="DVAS all-in-one RunPod image (runpod-v1)" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      dvas.base-image="${BASE_IMAGE}"
# Recorded in evidence model_info and on export certificates (evidence plan E1/E4)
ENV DVAS_GIT_SHA=${GIT_SHA}

EXPOSE 80 22
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
  CMD curl -fsS http://127.0.0.1/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "-s", "--", "/opt/dvas/deploy/bin/dvas-init.sh"]
