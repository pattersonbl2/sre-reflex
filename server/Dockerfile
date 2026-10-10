FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# fastapi/uvicorn/prometheus-client pinned to the versions resolved in uv.lock.
# transformers is bounded <5 only and the rest are unpinned: no lockfile covers them.
RUN pip install --no-cache-dir \
    "fastapi==0.141.1" "uvicorn[standard]==0.53.0" "prometheus-client==0.26.0" \
    "transformers>=4.56,<5" safetensors huggingface_hub sentencepiece protobuf \
    "typed-decisions @ git+https://github.com/kotoba-lang/typed-decisions@e4ed807630c061179a6834d77e18c43227e4f19d"
COPY model_server.py .
ENV HF_HOME=/models
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /models && chown -R app:app /models /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "model_server:app", "--host", "0.0.0.0", "--port", "8000"]
