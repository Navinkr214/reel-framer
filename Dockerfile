# Reel Framer for servers. Render builds this file (render.yaml); any Docker host can too:
#   docker build -t reel-framer . && docker run -p 8501:8501 -e APP_PASSWORD=... reel-framer
FROM python:3.13-slim

# ffmpeg: merging downloads and rendering. fontconfig + Noto fonts: captions in every
# script (Latin, Indic and the other core scripts, CJK, colour emoji). libfribidi0:
# Pillow's text shaping (raqm) loads it at run time; without it Indic text is unshaped.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg fontconfig fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji libfribidi0 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The app runs as this unprivileged user; the entrypoint only prepares its data folder as root.
RUN useradd --create-home app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Hosted: a password is required and there is no local browser to read logins from.
# Data lives where render.yaml mounts the persistent disk.
ENV REEL_FRAMER_HOSTED=1 \
    REEL_FRAMER_HOME=/var/data/reel_framer \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
