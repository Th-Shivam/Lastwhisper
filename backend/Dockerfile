FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system lastwhisper \
    && useradd --system --gid lastwhisper --home /app lastwhisper

WORKDIR /app
COPY backend/requirements.txt backend/constraints-cpu.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations
RUN mkdir -p /data/audio /models/whisper /models/embeddings \
    && chown -R lastwhisper:lastwhisper /app /data /models \
    && chmod 700 /data /data/audio /models /models/whisper /models/embeddings

USER lastwhisper
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/ready', timeout=3).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
