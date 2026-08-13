# ── Drone Ingest API — Dockerfile ──────────────────────────────────────────
# Multi-stage build: keeps the final image slim (~200 MB).

FROM python:3.11-slim AS base

# System dependencies for cryptography (python-jose) + curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependency layer (cached separately from source) ──────────────────────
FROM base AS deps
COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# ── Final image ───────────────────────────────────────────────────────────
FROM base AS final

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy application source
COPY . .

# Non-root user for security
RUN useradd -r -s /bin/false appuser && chown -R appuser /app
USER appuser

# Expose uvicorn port
EXPOSE 8000

# Health check used by Docker Compose
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start with 4 workers (matches the plan)
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-level", "info", \
     "--access-log"]
