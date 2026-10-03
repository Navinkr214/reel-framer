"""The Settings tab: everything a framed video uses, saved on every change,
with a live preview drawn from a recent video.

Sections: banners (top / bottom: upload, replace, remove), frame and layout,
background, captions (top / bottom), output (quality, encoding speed, folder),
Instagram login (a browser's login or a cookies.txt; on a server only the file,
and locally an export of one site's login for a server), maintenance (yt-dlp
version and update, kept downloads, finished videos, free space).

Each run rebuilds a Settings object from the widgets and saves it when it
differs from the saved file. An upload is stored once (assets.py), then its
uploader is emptied by changing its key, so no file is stored twice. When an
action changes a value that a widget also shows (e.g. an uploaded font becomes
the selected font), the widget's state is set too, because Streamlit gives a
widget's own state priority over the value it is created with.

The preview is redrawn only when the source, the time or the settings change,
and on servers with less than one CPU only when asked (hosting.fractional_cpu);
the latest one is kept in the session (one image per browser session).

Not in here: what the values do (pipeline.py) or the Settings model (settings.py).
Called by: app.py.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
from typing import Callable

import streamlit as st
from PIL import Image

from .. import assets, cookies_export, downloader, encoder, hosting, layout, paths, pipeline, server_defaults, updater
from .. import settings as store
from ..compose import ComposeError
from ..media_probe import ProbeError, probe
from ..settings import Settings, TextStyle
from ..text import backend
from .widgets import choice, human_size, slider

FRAME_SHAPES = {
    "source": "Same as the video",
    "9:16": "9:16 — Reels, Shorts, TikTok",
    "4:5": "4:5 — Instagram feed",
    "1:1": "1:1 — Square",
    "16:9": "16:9 — Landscape",
}
_CUSTOM = "custom"
PLACEMENT_LABELS = {
    "over_banner": "On the banner",
    "band": "Own strip next to the video",
    "over_video": "On the video",
}
ALIGN_LABELS = {"center": "Centre", "left": "Left", "right": "Right"}
_PREVIEW_ERRORS = (pipeline.JobError, ComposeError, ProbeError, layout.LayoutError, OSError)
# Keeps the preview column in view while the settings column scrolls. It finds the column by
# the marker element inside it; if Streamlit's markup changes, the preview simply scrolls.
_STICKY_PREVIEW = """<span class="reel-framer-preview"></span><style>
div[data-testid="stColumn"]:has(.reel-framer-preview) {
  position: sticky; top: 0; align-self: flex-start; max-height: 100vh; overflow-y: auto;
}
</style>"""


def render() -> None:
    saved = store.load()
    s = store.from_json(store.to_json(saved))  # working copy the widgets edit

    def commit() -> None:
        """Save now and redraw the page (after an upload or a removal)."""
        store.save(s)
        st.rerun()

    left, right = st.columns([3, 2], gap="large")
    with left:
        _flash()
        _banners(s, commit)
        _frame(s)
        _captions(s, commit)
        _output(s)
        _login(s, commit)
        _maintenance(s)
    if store.to_json(s) != store.to_json(saved):
        store.save(s)
    with right:
        _preview(s)


def _flash() -> None:
    message = st.session_state.pop("settings_error", None)
    if message:
        st.error(message)


def _round(key: str) -> int:
    """Generation number of an uploader; bumping it gives the uploader a new, empty key."""
    return st.session_state.setdefault(f"{key}_round", 0)


def _next_round(key: str) -> None:
    st.session_state[f"{key}_round"] = _round(key) + 1


# --- Banners -----------------------------------------------------------------

def _banners(s: Settings, commit: Callable[[], None]) -> None:
    st.subheader("Banners")
    st.caption("Shown for the whole video: images stay still, GIFs and clips loop.")
    for column, side in zip(st.columns(2), ("top", "bottom")):
        with column:
            _banner_slot(s, side, commit)


def _banner_slot(s: Settings, side: str, commit: Callable[[], None]) -> None:
    field = f"{side}_banner"
    name = getattr(s, field)
    st.markdown(f"**{side.title()} banner**")
    if assets.exists(name):
        if assets.is_image(name):
            st.image(str(assets.path_of(name)))
        else:
            st.video(assets.path_of(name), loop=True, muted=True)
        if st.button("Remove", key=f"remove_{field}"):
            setattr(s, field, "")
            assets.remove_if_unused(name, {s.top_banner, s.bottom_banner})
            commit()
    upload = st.file_uploader(
        "Replace" if name else "Upload an image, GIF or short clip", key=f"{field}_{_round(field)}"
    )
    if upload is not None:
        _next_round(field)
        try:
            stored = assets.save_banner(upload.name, upload.getvalue())
        except assets.AssetError as exc:
            st.session_state["settings_error"] = str(exc)
            st.rerun()
        setattr(s, field, stored)
        if name and name != stored:
            assets.remove_if_unused(name, {s.top_banner, s.bottom_banner})
        commit()


# --- Frame, layout, background ----------------------------------------------

def _frame(s: Settings) -> None:
    st.subheader("Frame & layout")
    shapes = dict(FRAME_SHAPES)
    if s.frame_aspect not in shapes:
        shapes[s.frame_aspect] = f"{s.frame_aspect} — custom"
    shapes[_CUSTOM] = "Custom…"
    picked = choice("Frame shape", shapes, s.frame_aspect, "frame_aspect")
    if picked == _CUSTOM:
        text = st.text_input("Custom shape as W:H", key="frame_custom", placeholder="for example 2:3")
        try:
            if text and layout.parse_aspect(text):
                s.frame_aspect = text
        except layout.LayoutError as exc:
            st.warning(str(exc))
    else:
        s.frame_aspect = picked
    s.frame_short_side = int(st.number_input(
        "Short side in pixels (0 = same as the video)",
        min_value=0, value=s.frame_short_side, step=encoder.CHROMA_ALIGN, key="frame_short_side",
        help="The frame's width for tall shapes, its height for wide ones.",
    ))
    s.banner_max_pct = slider("Banner max height (% of the frame height)", 5.0, 60.0, s.banner_max_pct, 1.0,
                              "banner_max_pct", help="Each banner spans the frame width at its own shape, "
                              "and is shrunk if it would be taller than this.")
    s.stack = choice("Banner position", {"edges": "At the frame's top and bottom edges",
                                         "hug": "Touching the video"}, s.stack, "stack", radio=True)
    s.video_scale_pct = slider("Video size (% of the room the banners leave)", 30.0, 100.0,
                               s.video_scale_pct, 1.0, "video_scale_pct")
    s.background = choice("Background around the video", {"blur": "Blurred copy of the video",
                                                           "color": "Solid colour"},
                          s.background, "background", radio=True)
    if s.background == "blur":
        s.blur_pct = slider("Blur strength", 0.5, 10.0, s.blur_pct, 0.5, "blur_pct")
    else:
        s.background_color = st.color_picker("Background colour", s.background_color, key="background_color")


# --- Captions ----------------------------------------------------------------

@st.cache_resource(show_spinner="Reading installed fonts…")
def _installed_faces():
    return backend.list_faces()


def _captions(s: Settings, commit: Callable[[], None]) -> None:
    st.subheader("Captions")
    st.caption("Optional text in any language, emoji included. Leave empty for none.")
    if backend.name() == "none":
        st.warning("No system font service found: captions use only the font you upload, "
                   "without automatic fallback for other scripts.")
    faces = _installed_faces()
    for side in ("top", "bottom"):
        style: TextStyle = getattr(s, f"{side}_text")
        title = f"{side.title()} caption" + (" ✓" if style.text.strip() else "")
        with st.expander(title, expanded=bool(style.text.strip())):
            _caption_editor(side, style, faces, commit)
    s.margin_pct = slider("Caption side margin (% of the frame width)", 0.0, 15.0, s.margin_pct, 0.5, "margin_pct")


def _caption_editor(side: str, style: TextStyle, faces, commit: Callable[[], None]) -> None:
    key = f"{side}_text"
    style.text = st.text_area("Text (Enter starts a new line)", style.text, key=f"{key}_text")
    style.placement = choice("Where", PLACEMENT_LABELS, style.placement, f"{key}_placement", radio=True)

    # A font uploaded on the previous run becomes the picker's value before the picker exists
    # (Streamlit forbids changing a widget's state once the widget is on the page).
    pending = st.session_state.pop(f"{key}_font_pending", None)
    if pending is not None:
        st.session_state[f"{key}_font"] = pending
    fonts = {"": "System font (picks the right font for each language)"}
    if style.font.startswith("asset:"):
        fonts[style.font] = f"Uploaded: {style.font.removeprefix('asset:')}"
    fonts.update({f.id: f"{f.family} — {f.style}" if f.style else f.family for f in faces})
    if style.font not in fonts:
        fonts[style.font] = style.font.removeprefix("ps:")
    style.font = choice("Font", fonts, style.font, f"{key}_font")
    if not style.font:
        style.bold = st.toggle("Bold", style.bold, key=f"{key}_bold")
    upload = st.file_uploader("…or upload a font file (.ttf / .otf)", key=f"{key}_fontfile_{_round(key + '_font')}")
    if upload is not None:
        _next_round(key + "_font")
        try:
            stored = assets.save_font(upload.name, upload.getvalue())
        except assets.AssetError as exc:
            st.session_state["settings_error"] = str(exc)
            st.rerun()
        style.font = f"asset:{stored}"
        st.session_state[f"{key}_font_pending"] = style.font
        commit()

    colours = st.columns(3)
    style.color = colours[0].color_picker("Text colour", style.color, key=f"{key}_color")
    style.outline_color = colours[1].color_picker("Outline colour", style.outline_color, key=f"{key}_outline_color")
    style.box_color = colours[2].color_picker("Box colour", style.box_color, key=f"{key}_box_color")
    style.size_pct = slider("Text size (% of the frame width)", 2.0, 15.0, style.size_pct, 0.25, f"{key}_size")
    style.outline_pct = slider("Outline thickness (% of the text size, 0 = none)", 0.0, 25.0,
                               style.outline_pct, 0.5, f"{key}_outline")
    style.box_opacity_pct = slider("Box behind the text: opacity (0 = no box)", 0.0, 100.0,
                                   style.box_opacity_pct, 5.0, f"{key}_box")
    style.align = choice("Align", ALIGN_LABELS, style.align, f"{key}_align", radio=True)
    style.line_spacing = slider("Line spacing", 0.8, 2.0, style.line_spacing, 0.05, f"{key}_spacing")


# --- Output, login, maintenance ---------------------------------------------

def _output(s: Settings) -> None:
    st.subheader("Output")
    s.quality = choice("Quality", {"match": "Match the source video (recommended)",
                                   "crf": "Constant quality (CRF)"}, s.quality, "quality", radio=True)
    if s.quality == "crf":
        # H.264 quantisers run 0-51 for 8-bit video; lower = better quality, bigger file.
        s.crf = int(st.slider("CRF (lower = better and bigger)", 0, 51, min(51, max(0, s.crf)), key="crf"))
    encoders = encoder.video_encoders()
    default = encoder.encoder_name()
    if len(encoders) > 1:
        options = {"": f"Standard: {encoders.get(default, default)}"} | {
            name: description for name, description in encoders.items() if name != default}
        s.video_encoder = choice(
            "Encoder", options, s.video_encoder, "video_encoder",
            help="A hardware encoder (e.g. VideoToolbox on a Mac) renders with far less CPU; "
                 "the standard encoder gives the best picture for the file size.",
        )
    speeds = encoder.presets(encoder.chosen_encoder(s.video_encoder))
    if speeds:
        s.encoder_preset = choice(
            "Encoding speed (fastest first)", {"": "Encoder default"} | {p: p for p in speeds},
            s.encoder_preset, "encoder_preset",
            help="Faster presets use less CPU and make slightly bigger files. On a small server, "
                 "veryfast renders roughly twice as fast as the default.",
        )
    s.output_dir = st.text_input("Save videos to (blank = default folder)", s.output_dir, key="output_dir").strip()
    try:
        st.caption(f"Videos are saved in `{paths.output_dir(s.output_dir)}` · encoder: {encoder.encoder_name()}")
    except OSError as exc:
        st.warning(f"That folder can't be used: {exc}")
        s.output_dir = ""


def _login(s: Settings, commit: Callable[[], None]) -> None:
    st.subheader("Instagram login (optional)")
    if hosting.hosted():
        st.caption("Instagram often asks servers to log in. Upload a cookies.txt with your Instagram "
                   "login: in Reel Framer on your own computer, open Settings → Instagram login → "
                   "Export a login, then upload that file here (or add it to the host as a secret "
                   "file, which also survives restarts).")
        if hosting.host_cookies_file():
            st.caption("✓ The server provides an Instagram login (secret file). "
                       "A cookies.txt uploaded below is used instead.")
        s.cookies_browser = ""  # a server has no browser to read a login from
    else:
        st.caption("Instagram often shows reels only to logged-in visitors. Pick a browser where you are "
                   "logged in to Instagram, or upload a cookies.txt exported from it.")
        browsers = {"": "Don't use a browser login"} | {b: b.title() for b in downloader.supported_browsers()}
        s.cookies_browser = choice("Use the login saved in this browser", browsers, s.cookies_browser,
                                   "cookies_browser")
        _export_login(s.cookies_browser)
    if assets.exists(s.cookies_file):
        st.write(f"cookies.txt in use: `{s.cookies_file}`")
        if st.button("Remove cookies.txt", key="remove_cookies"):
            assets.remove_if_unused(s.cookies_file, set())
            s.cookies_file = ""
            commit()
    upload = st.file_uploader("cookies.txt (Netscape format)", key=f"cookies_{_round('cookies')}")
    if upload is not None:
        _next_round("cookies")
        old = s.cookies_file
        s.cookies_file = assets.save_cookies(upload.name, upload.getvalue())
        if old and old != s.cookies_file:
            assets.remove_if_unused(old, set())
        commit()


def _export_login(current_browser: str) -> None:
    with st.expander("Export a login for a server copy of this app"):
        st.caption("Reads one site's cookies from a browser on this computer and gives you a cookies.txt "
                   "to upload to a hosted Reel Framer. Only that site's cookies are included, never your "
                   "other logins.")
        browsers = downloader.supported_browsers()
        browser = st.selectbox("Browser", browsers, format_func=str.title, key="export_browser",
                               index=browsers.index(current_browser) if current_browser in browsers else 0)
        site = st.text_input("Site", "instagram.com", key="export_site").strip()
        if st.button("Read the login", key="export_read"):
            try:
                st.session_state["exported_login"] = (site, cookies_export.export(browser, site))
            except cookies_export.ExportError as exc:
                st.session_state.pop("exported_login", None)
                st.error(str(exc))
        exported = st.session_state.get("exported_login")
        if exported:
            site_name, data = exported
            st.download_button(f"Download {site_name} cookies.txt", data=data, file_name=f"{site_name}-cookies.txt",
                               mime="text/plain", on_click="ignore", key="export_download")
            st.caption("Keep this file private: anyone holding it is logged in as you.")


def _maintenance(s: Settings) -> None:
    with st.expander("Maintenance"):
        st.write(f"Downloader: yt-dlp {downloader.version()}")
        st.caption("Instagram changes often; if links stop downloading, update yt-dlp and restart the app"
                   + (" (on a server, redeploying also installs the newest yt-dlp)." if hosting.hosted() else "."))
        if st.button("Update yt-dlp"):
            with st.spinner("Updating yt-dlp…"):
                if hosting.packaged():  # no pip inside a packaged app: fetch the wheel from PyPI
                    try:
                        st.success(f"yt-dlp {updater.update()} is installed. "
                                   "Close Reel Framer and open it again to use it.")
                    except updater.UpdateError as exc:
                        st.error(str(exc))
                else:
                    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                                          capture_output=True, text=True)
                    if proc.returncode == 0:
                        st.success("Updated. Restart the app (stop it and run ./run.sh again) to use it.")
                    else:
                        st.error(proc.stderr.strip() or proc.stdout.strip())
        sources = pipeline.recent_sources()
        size = sum(p.stat().st_size for p in sources)
        st.write(f"Videos kept for previews and re-renders: {len(sources)} ({human_size(size)}) "
                 f"in `{paths.downloads_dir()}`")
        if sources and st.button("Delete them"):
            for path in sources:
                path.unlink(missing_ok=True)
            st.session_state.pop("preview", None)
            st.rerun()
        try:
            out_dir = paths.output_dir(s.output_dir)
        except OSError:
            return
        finished = [p for p in out_dir.iterdir() if p.is_file() and p.suffix == ".mp4"]
        st.write(f"Finished videos: {len(finished)} ({human_size(sum(p.stat().st_size for p in finished))}) "
                 f"in `{out_dir}` · free space: {human_size(shutil.disk_usage(out_dir).free)}")
        if finished and st.checkbox("I have downloaded what I need; delete the finished videos", key="confirm_outputs"):
            if st.button("Delete finished videos"):
                for path in finished:
                    path.unlink(missing_ok=True)
                st.rerun()
        if not hosting.hosted():
            st.markdown("**Server starting settings**")
            st.caption("Writes these settings and their banner and font files to the project's "
                       "`defaults/` folder. A hosted copy without a disk (Render's free plan) starts "
                       "from it after every restart; push the folder to GitHub to update the server. "
                       "Logins are never included.")
            if st.button("Save as the server's starting settings", key="save_defaults"):
                folder = server_defaults.save(s)
                st.success(f"Saved to `{folder}`. Push it to GitHub and the server picks it up.")


# --- Preview -----------------------------------------------------------------

def _preview(s: Settings) -> None:
    st.markdown(_STICKY_PREVIEW, unsafe_allow_html=True)
    st.subheader("Preview")
    sources = pipeline.recent_sources()
    if not sources:
        st.info("The preview uses your recent videos. Create one on the Create tab "
                "(a link or an uploaded file), then come back here.")
        return
    source = st.selectbox("Video", sources, format_func=lambda p: p.name, key="preview_source")
    try:
        duration = probe(source).duration or 0.0
    except ProbeError as exc:
        st.error(str(exc))
        return
    at = (st.slider("Time (seconds)", 0.0, duration, duration / 2, key=f"preview_at_{source.name}")
          if duration > 0 else 0.0)
    automatic = st.toggle(
        "Redraw on every change", value=not hosting.fractional_cpu(), key="preview_auto",
        help="Starts off on servers with less than one CPU, where one preview frame takes many seconds.",
    )
    key = (str(source), source.stat().st_mtime_ns, store.to_json(s), at)
    cached = st.session_state.get("preview")
    if not cached or cached[0] != key:
        if automatic or st.button("Update the preview", key="preview_update"):
            with st.spinner("Drawing the preview…"):
                try:
                    cached = (key, pipeline.preview(source, s, at), "")
                except _PREVIEW_ERRORS as exc:
                    cached = (key, None, str(exc))
            st.session_state["preview"] = cached
        elif cached:
            st.caption("Settings changed since this preview was drawn.")
    if not cached:
        return
    _, png, error = cached
    if error:
        st.error(error)
    else:
        width, height = Image.open(io.BytesIO(png)).size
        st.image(png, caption=f"{width} × {height} px at {cached[0][3]:.1f} s")
