FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /analytics

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY person/requirements.txt /tmp/requirements.txt
RUN pip install --default-timeout=100 --no-cache-dir -r /tmp/requirements.txt

# Download ArcFace R100 ONNX model from InsightFace buffalo_l pack
RUN pip install --no-cache-dir insightface && \
    python -c "\
from insightface.app import FaceAnalysis; \
app = FaceAnalysis(name='buffalo_l', root='/tmp/insightface'); \
app.prepare(ctx_id=-1, det_size=(112,112))" && \
    cp /tmp/insightface/models/buffalo_l/w600k_r50.onnx /tmp/arcface_r100.onnx && \
    rm -rf /tmp/insightface && \
    pip uninstall -y insightface scikit-image

COPY shared/ /analytics/shared/
COPY person/ /analytics/person/

# Place ArcFace model in the models directory
RUN cp /tmp/arcface_r100.onnx /analytics/person/models/arcface_r100.onnx && \
    rm /tmp/arcface_r100.onnx

EXPOSE 8102

CMD ["uvicorn", "person.main:app", "--host", "0.0.0.0", "--port", "8102", "--workers", "1"]
