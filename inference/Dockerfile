# Model server image for the stage 1 search models (inference/server.py). Build context = repo root:
#   docker build -f inference/Dockerfile -t kosis-inference:gpu .
#   docker build -f inference/Dockerfile --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu -t kosis-inference:cpu .
# One image, one model per container: INFER_MODEL=embed | rerank (docker-compose.inference.yml runs both).
# Fine-tuned weights (.pt + .json) are NOT in the image: mount data/models at /models (read-only).
# The public base models (BAAI/bge-m3, BAAI/bge-reranker-v2-m3) are downloaded at build time (PRELOAD=1) so the
# server starts offline; PRELOAD=0 downloads them on first start into HF_HOME instead (mount a volume there).
FROM python:3.12-slim

# PyPI torch on Linux x86_64 is the CUDA build (runs on CPU too when no GPU is given); the cpu index makes a small image
ARG TORCH_INDEX=https://pypi.org/simple
ARG PRELOAD=1
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 HF_HOME=/opt/hf

WORKDIR /app
COPY inference/requirements.txt inference/requirements.txt
# versions = uv.lock of this repo (torch 2.15.0, sentence-transformers 6.2.0, transformers 5.22.1)
RUN pip install --index-url "${TORCH_INDEX}" --extra-index-url https://pypi.org/simple torch==2.15.0 && pip install -r inference/requirements.txt

RUN if [ "$PRELOAD" = "1" ]; then python -c "from sentence_transformers import CrossEncoder, SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu'); CrossEncoder('BAAI/bge-reranker-v2-m3', device='cpu')"; fi
# offline at run time when the base models are in the image (no surprise downloads on the server)
ENV HF_HUB_OFFLINE=${PRELOAD}

# Only the model loader of phase 2 (stdlib imports + torch / sentence-transformers); its package __init__ files are docstrings
COPY vendor/phase2/src/kosis_rag/__init__.py src/kosis_rag/__init__.py
COPY vendor/phase2/src/kosis_rag/adapters/__init__.py src/kosis_rag/adapters/__init__.py
COPY vendor/phase2/src/kosis_rag/adapters/models.py src/kosis_rag/adapters/models.py
COPY inference/__init__.py inference/server.py inference/
ENV PYTHONPATH=/app:/app/src

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=5 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8001') + '/health')"
CMD ["sh", "-c", "python -m inference.server --host 0.0.0.0 --port ${PORT:-8001}"]
