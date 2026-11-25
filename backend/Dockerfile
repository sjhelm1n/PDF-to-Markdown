FROM python:3.12-slim-bookworm
ARG PDF2MD_TORCH_BUILD=default
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt torch-cu130.txt ./
RUN case "$PDF2MD_TORCH_BUILD" in \
      default) pip install --no-cache-dir -r requirements.txt ;; \
      cu130) pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu130 -r torch-cu130.txt \
             && pip install --no-cache-dir -c torch-cu130.txt -r requirements.txt ;; \
      *) echo "PDF2MD_TORCH_BUILD must be default or cu130" >&2; exit 1 ;; \
    esac && pip check
COPY app ./app
ENV APP_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
