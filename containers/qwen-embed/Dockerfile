FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    TRANSFORMERS_CACHE=/models/hf \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r /app/requirements.txt

COPY embed_server.py /app/embed_server.py

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=20 \
  CMD curl -fsS http://127.0.0.1:8001/health || exit 1

ENTRYPOINT ["python", "/app/embed_server.py"]
CMD ["--model", "Qwen/Qwen3-Embedding-8B", "--host", "0.0.0.0", "--port", "8001", "--device", "cpu", "--dtype", "bfloat16", "--batch-size", "1", "--max-length", "8192", "--log-level", "INFO"]
