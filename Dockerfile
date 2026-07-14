FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    CHROMIUM_PATH=/usr/bin/chromium \
    REMOTION_CHROME_EXECUTABLE=/usr/bin/chromium

# deno → yt-dlp JS runtime for the YouTube ingest path (--js-runtimes deno).
# Pinned via build arg; installed into DENO_INSTALL/bin (on PATH via /usr/local/bin).
ARG DENO_VERSION=2.4.0
ENV DENO_INSTALL=/usr/local

# System deps for the full render path:
#   ffmpeg + fonts → banner/caption burn + composite
#   chromium       → Remotion overlay rendering (passed via --browser-executable)
#   nodejs (+npm)  → Remotion CLI (overlay PNG-sequence render)
#   curl/gnupg/ca-certificates → NodeSource setup, healthcheck, TLS
#   unzip          → prerequisite for the Deno installer archive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-montserrat \
        fonts-dejavu-core \
        chromium \
        curl \
        git \
        gnupg \
        ca-certificates \
        unzip \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://deno.land/install.sh | sh -s -- v${DENO_VERSION} \
    && deno --version \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first so source-only changes don't reinstall everything.
COPY pyproject.toml README.md ./
COPY src/ /app/src/
# yt-dlp  → download_crisp_source (video ingest)
# uv/uvx  → caption_words runs `uvx whisper-ctranslate2` for word timestamps
RUN pip install --upgrade pip && pip install . && pip install yt-dlp uv

# Remotion project's node deps (overlay renderer) from the lockfile, in its own
# layer so Python/source changes don't reinstall node_modules.
COPY remotion/package.json remotion/package-lock.json /app/remotion/
RUN cd /app/remotion && npm ci --no-audit --no-fund

# Copy the rest (entry shim, remotion sources, web, scripts).
COPY . /app/

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8002}/health || exit 1

CMD ["python", "main.py"]
