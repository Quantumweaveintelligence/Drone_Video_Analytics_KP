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
ARG VALKEY_IMAGE=valkey/valkey:7.2
# Pin MinIO/mc to the versions you run locally (Phase 0: tools/collect_versions.sh)
ARG MINIO_IMAGE=minio/minio:RELEASE.2025-04-22T22-12-26Z
ARG MC_IMAGE=minio/mc:RELEASE.2025-04-16T18-13-26Z

# ── Stage 1: cloud frontend build ─────────────────────────────────────────────
FROM node:20-bookworm AS frontend
WORKDIR /src
COPY DVAS_Dashboard/frontend/package.json DVAS_Dashboard/frontend/package-lock.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund
COPY DVAS_Dashboard/frontend/ ./
ENV REACT_APP_DEPLOYMENT_TARGET=cloud REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false CI=false
RUN npm run build && test -f build/index.html

# ── Stage 2: binaries from official images ────────────────────────────────────
FROM ${KEYCLOAK_IMAGE} AS keycloak
FROM ${VALKEY_IMAGE}   AS valkey
FROM ${MINIO_IMAGE}    AS minio
FROM ${MC_IMAGE}       AS mc

# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM ${BASE_IMAGE}
ARG PY=/usr/bin/python3
ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Kolkata PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 LANG=C.UTF-8 LC_ALL=C.UTF-8

# Don't let the postgres package create a default cluster (ours lives on /workspace)
RUN mkdir -p /etc/postgresql-common && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf
RUN apt-get update && apt-get install -y --no-install-recommends \
      nginx supervisor tini openssh-server curl ca-certificates jq rsync tzdata util-linux \
      postgresql-16 postgresql-client-16 openjdk-21-jre-headless \
      ffmpeg libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 libxrender1 python3-venv \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

# Keycloak, Valkey, MinIO, mc
COPY --from=keycloak /opt/keycloak /opt/keycloak
COPY --from=valkey /usr/local/bin/valkey-server /usr/local/bin/valkey-cli /usr/local/bin/
COPY --from=minio /usr/bin/minio /usr/local/bin/minio
COPY --from=mc /usr/bin/mc /usr/local/bin/mc
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true --metrics-enabled=false \
 && valkey-server --version && minio --version && mc --version

# Python environments. torch/torchvision come from the base image: the AI venvs
# see them through --system-site-packages and a constraints file pins them so
# pip never replaces them (local version label stripped: "2.8.0+cu128" -> "2.8.0").
RUN ${PY} -c "import sys;assert sys.version_info[:2]>=(3,10),sys.version" && \
    ${PY} -c "import torch,torchvision;print(f'torch=={torch.__version__.split(\"+\")[0]}\ntorchvision=={torchvision.__version__.split(\"+\")[0]}')" > /opt/torch-constraints.txt && \
    cat /opt/torch-constraints.txt
COPY deploy/runpod/requirements/ /opt/dvas/requirements/
RUN set -e; \
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
  /opt/venvs/ai/bin/python -c "import torch;assert torch.__version__.startswith('2.8'), torch.__version__;print('torch', torch.__version__)"; \
  /opt/venvs/ai/bin/python -c "import onnxruntime as o;print('onnxruntime providers', o.get_available_providers())"

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
# byte-compile everything as a Python 3.12 smoke test (F16).
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

EXPOSE 80 22
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
  CMD curl -fsS http://127.0.0.1/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/dvas/deploy/bin/dvas-init.sh"]
