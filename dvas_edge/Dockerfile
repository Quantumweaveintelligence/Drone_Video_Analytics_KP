FROM python:3.11-slim

WORKDIR /app

# System dependencies for OpenCV headless (Debian trixie compatible)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .

# Build a Docker-compatible requirements file:
#  - Replace opencv-python with headless version (no GUI in Docker)
#  - Remove GPU-only packages (cupy-cuda, nvidia-ml-py3) that need CUDA toolkit
#  - Fix Windows CRLF line endings
RUN sed 's/\r$//' requirements.txt \
    | sed 's/opencv-python>=/opencv-python-headless>=/' \
    | grep -v 'cupy-cuda' \
    | grep -v 'nvidia-ml-py3' \
    > /tmp/requirements_docker.txt && \
    pip install --default-timeout=100 --no-cache-dir -r /tmp/requirements_docker.txt && \
    rm /tmp/requirements_docker.txt

# Copy source code (video files excluded via .dockerignore)
COPY . .

# Create necessary directories
RUN mkdir -p buffer_storage logs

EXPOSE 8765

CMD ["python", "edge_api_server.py"]
