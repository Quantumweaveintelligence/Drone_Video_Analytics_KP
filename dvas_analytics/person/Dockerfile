FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /analytics

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY person/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY shared/ /analytics/shared/
COPY person/ /analytics/person/

EXPOSE 8102

CMD ["uvicorn", "person.main:app", "--host", "0.0.0.0", "--port", "8102", "--workers", "1"]
