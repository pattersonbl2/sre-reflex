FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "prometheus-client>=0.20" \
    "transformers>=4.56,<5" safetensors huggingface_hub sentencepiece protobuf \
    "typed-decisions @ git+https://github.com/kotoba-lang/typed-decisions@e4ed807630c061179a6834d77e18c43227e4f19d"
COPY model_server.py .
ENV HF_HOME=/models
EXPOSE 8000
CMD ["uvicorn", "model_server:app", "--host", "0.0.0.0", "--port", "8000"]
