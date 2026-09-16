FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /analytics

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY vessel/requirements.txt /tmp/requirements.txt
RUN pip install --default-timeout=100 --no-cache-dir -r /tmp/requirements.txt

COPY shared/ /analytics/shared/
COPY vessel/ /analytics/vessel/

EXPOSE 8106

CMD ["uvicorn", "vessel.main:app", "--host", "0.0.0.0", "--port", "8106", "--workers", "1"]
