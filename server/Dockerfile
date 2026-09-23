# typed-decisions requires Python >=3.12; the pytorch/pytorch base image ships conda 3.11,
# which made pip backtrack until ResolutionTooDeep. The PyPI torch wheel bundles its own
# CUDA runtime, so a plain Python base only needs the host NVIDIA driver.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
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
