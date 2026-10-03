"""Reel Framer: Streamlit entry point.

Run:  ./run.sh            (first run creates .venv and installs requirements)
 or:  .venv/bin/streamlit run app.py

Tabs: Create (paste links / upload videos -> framed MP4s to watch and download)
and Settings (banners, captions, frame and layout, output, Instagram login,
live preview). With $APP_PASSWORD set (always on a hosted copy) the page asks
for it first. The code lives in reel_framer/ (see its __init__.py).
"""
import shutil

import streamlit as st

from reel_framer import server_defaults
from reel_framer.text import backend as font_backend
from reel_framer.ui import create_tab, settings_tab
from reel_framer.ui.password_gate import require_password

st.set_page_config(page_title="Reel Framer", page_icon="🎞️", layout="wide")
st.title("Reel Framer")
require_password()
server_defaults.seed_if_fresh()  # a hosted copy without settings starts from defaults/
font_backend.warm_up()           # Windows: read the installed fonts in the background, once
st.caption("Paste a reel link and get the video back inside your top and bottom banners.")

missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
if missing:
    st.error(f"{' and '.join(missing)} not found. Install ffmpeg (macOS: `brew install ffmpeg`) "
             "and start the app again.")
    st.stop()

create, setup = st.tabs(["Create", "Settings"])
# Settings runs first (it still shows as the second tab): it saves any change made in
# this rerun, so the Create tab below always reads the settings the user just set.
with setup:
    settings_tab.render()
with create:
    create_tab.render()
