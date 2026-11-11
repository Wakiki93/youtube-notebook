# auto-clip-poster: AutoClip (clipping) + our loop, in one container.
# Postiz (posting) runs separately: Postiz Cloud, or its official Docker setup in vendor/postiz-docker-compose.
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY auto_clip_poster ./auto_clip_poster

# WHISPER=0 skips local transcription (about 1 GB smaller); videos without captions then fail to clip.
ARG WHISPER=1
RUN pip install --no-cache-dir ".[clip,scout]" \
 && if [ "$WHISPER" = "1" ]; then pip install --no-cache-dir "faster-whisper>=1.0"; fi

ENV ACP_DATA_DIR=/data \
    AUTOCLIP_DATA_DIR=/data/autoclip \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

ENTRYPOINT ["acp"]
CMD ["loop"]
