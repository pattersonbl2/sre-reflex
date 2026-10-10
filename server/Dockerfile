# Python 3.12 is required: typed-decisions declares requires-python >=3.12. The
# previous pytorch/pytorch base image ships conda Python 3.11, so pip could not
# satisfy it and backtracked through unrelated packages until
# ResolutionTooDeep (the release `images` job failed on every push to main).
# torch (with its CUDA libraries) comes from PyPI as a dependency of transformers.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN uv pip install --system --no-cache \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "prometheus-client>=0.20" \
    "transformers>=4.56,<5" safetensors huggingface_hub sentencepiece protobuf \
    "typed-decisions @ git+https://github.com/kotoba-lang/typed-decisions@e4ed807630c061179a6834d77e18c43227e4f19d"
COPY model_server.py .
ENV HF_HOME=/models
EXPOSE 8000
CMD ["uvicorn", "model_server:app", "--host", "0.0.0.0", "--port", "8000"]
