# Build context: repository root (needs backend/ and data/knowledge_base/)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface

WORKDIR /app/backend

# CPU-only PyTorch keeps the image far smaller than the default CUDA build
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# Bake the embedding and local Whisper models into the image so the container starts without internet.
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
ARG STT_LOCAL_MODEL=openai/whisper-base.en
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')" \
 && python -c "from transformers import pipeline; pipeline('automatic-speech-recognition', model='${STT_LOCAL_MODEL}')"
ENV MODELS_OFFLINE=true

COPY backend/ /app/backend/
COPY data/knowledge_base/ /app/data/knowledge_base/

RUN useradd --create-home voiceops && mkdir -p /models && chown -R voiceops /models
USER voiceops

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
