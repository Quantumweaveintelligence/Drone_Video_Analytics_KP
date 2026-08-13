FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /analytics

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
    libgomp1 wget curl \
    && rm -rf /var/lib/apt/lists/*

# PaddlePaddle GPU (CUDA 12.x), fall back to CPU if GPU build fails
RUN pip install --default-timeout=100 --no-cache-dir paddlepaddle-gpu==2.6.2 \
    -f https://www.paddlepaddle.org.cn/whl/linux/mkl/avx/stable.html 2>/dev/null \
 || \
    pip install --default-timeout=100 --no-cache-dir paddlepaddle==2.6.2

# Core ML and API deps
RUN pip install --default-timeout=100 --no-cache-dir \
    torch torchvision ultralytics \
    fastapi "uvicorn[standard]" \
    python-multipart pydantic \
    asyncpg "redis[hiredis]" \
    opencv-python-headless numpy pandas \
    miniopy-async httpx

# PaddleOCR 2.9.1 — fully compatible with PaddlePaddle 2.6.2 (fast, accurate)
RUN pip install --default-timeout=100 --default-timeout=1000 --no-cache-dir "paddleocr==2.9.1"

COPY shared/ /analytics/shared/
COPY anpr/ /analytics/anpr/

EXPOSE 8104

CMD ["uvicorn", "anpr.main:app", "--host", "0.0.0.0", "--port", "8104", "--workers", "1"]
