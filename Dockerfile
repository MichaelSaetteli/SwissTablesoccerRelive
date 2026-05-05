# Video pipeline NAS - Synology DS1522+ container.
#
# Bundles the four packages (pipeline, watcher, web, youtube) plus FFmpeg
# into a slim Python 3.11 image. Runs the Flask app behind Waitress and
# spawns one folder watcher per enabled discipline at startup.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Zurich \
    VIDEO_PIPELINE_DATA_DIR=/data \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=5000

# System dependencies. ffmpeg is the only heavy binary; tini gives us a
# proper PID 1 so SIGTERM from `docker stop` reaches our Python process.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        tini \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so layer caching survives source edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application source - exclude tests, configs and dev artefacts via .dockerignore.
COPY pipeline/ ./pipeline/
COPY watcher/ ./watcher/
COPY web/ ./web/
COPY youtube/ ./youtube/

# Drop privileges. /data is mounted by the operator and must be writable
# by uid 1000 (briefing s.8). On Synology DSM the operator chowns the
# host directory accordingly.
RUN useradd -r -u 1000 -m -s /usr/sbin/nologin pipeline \
    && chown -R pipeline:pipeline /app

USER pipeline

EXPOSE 5000

# tini reaps zombies and forwards signals; exec form keeps Python in PID 1
# of its own subprocess group so Ctrl-C / docker stop work cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "web.app"]
