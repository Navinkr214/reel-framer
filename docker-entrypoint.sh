#!/bin/sh
# Container start: make the data folder writable for the "app" user (a host disk is often
# mounted there owned by root), then run Streamlit as that user on all interfaces, on the
# port the host asks for ($PORT on Render; Streamlit's own default otherwise).
# Source files never change inside the image, so Streamlit's file watcher is off.
set -eu

run_app() {
  exec "$@" streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port="${PORT:-8501}" \
    --server.headless=true \
    --server.fileWatcherType=none
}

mkdir -p "$REEL_FRAMER_HOME"
if [ "$(id -u)" = 0 ]; then
  if [ "$(stat -c %U "$REEL_FRAMER_HOME")" != app ]; then
    chown -R app:app "$REEL_FRAMER_HOME"
  fi
  # A host secret file (Render mounts them under /etc/secrets, readable by root only):
  # give the app its own copy, which yt-dlp may also update with refreshed cookies.
  if [ -n "${REEL_FRAMER_COOKIES_FILE:-}" ] && [ -f "$REEL_FRAMER_COOKIES_FILE" ]; then
    copy="$REEL_FRAMER_HOME/.host-cookies.txt"
    install -m 600 -o app -g app "$REEL_FRAMER_COOKIES_FILE" "$copy"
    REEL_FRAMER_COOKIES_FILE="$copy"
    export REEL_FRAMER_COOKIES_FILE
  fi
  run_app setpriv --reuid=app --regid=app --init-groups env HOME=/home/app
fi
run_app env
