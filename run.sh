#!/usr/bin/env bash
# Start Reel Framer. The first run creates .venv and installs requirements.txt;
# later runs reinstall only when requirements.txt has changed.
# PYTHON=/path/to/python3 ./run.sh picks the interpreter for a new .venv.
set -euo pipefail
cd "$(dirname "$0")"

for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Reel Framer needs $tool. Install ffmpeg (macOS: brew install ffmpeg)."; exit 1; }
done

[ -x .venv/bin/python ] || "${PYTHON:-python3}" -m venv .venv
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  .venv/bin/python -m pip install --quiet --prefer-binary -r requirements.txt
  touch .venv/.installed
fi
exec .venv/bin/streamlit run app.py "$@"
