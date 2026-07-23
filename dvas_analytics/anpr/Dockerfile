FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /analytics

# System dependencies for OpenCV + PaddlePaddle
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
    libgomp1 wget curl \
    && rm -rf /var/lib/apt/lists/*

# Install PaddlePaddle GPU (CUDA 12.x compatible wheel)
# Falls back to CPU-only paddle if GPU install fails
RUN pip install --no-cache-dir paddlepaddle-gpu==2.6.2 \
    -f https://www.paddlepaddle.org.cn/whl/linux/mkl/avx/stable.html 2>/dev/null || \
    pip install --no-cache-dir paddlepaddle==2.6.2

# Install core ML and API dependencies
RUN pip install --no-cache-dir \
    torch torchvision ultralytics \
    fastapi "uvicorn[standard]" \
    python-multipart pydantic \
    asyncpg "redis[hiredis]" \
    opencv-python-headless numpy pandas

# Install PaddleOCR (after paddlepaddle is installed)
RUN pip install --no-cache-dir paddleocr

# Copy shared utilities and ANPR service code
COPY shared/ /analytics/shared/
COPY anpr/ /analytics/anpr/

EXPOSE 8104

CMD ["uvicorn", "anpr.main:app", "--host", "0.0.0.0", "--port", "8104", "--workers", "1"]
