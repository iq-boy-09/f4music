FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# FFmpeg is required for MP3/video conversion. build-essential lets TgCrypto
# compile on platforms where a prebuilt wheel is unavailable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg build-essential ca-certificates \
    && chmod 755 /usr/bin/ffmpeg /usr/bin/ffprobe \
    && /usr/bin/ffmpeg -hide_banner -version >/dev/null \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .
RUN mkdir -p downloads

CMD ["python", "bot.py"]
