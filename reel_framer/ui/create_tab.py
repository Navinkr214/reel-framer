"""The Create tab: paste links (one per line) and/or upload videos, press
Create, and get each video back framed with the saved Settings.

Every link or file runs in its own status box with live progress; a failure
is shown for that item and the rest still run. Finished videos (Download
button, saved path) and failures stay listed for this browser session until
"Clear list"; every finished video in the output folder can also be downloaded
from "All finished videos", so a render whose page was closed or reloaded on a
slow server is not lost. Downloads read the file only when clicked. Only one result shows
a player at a time (the newest, or the one whose Play was pressed): Streamlit
keeps every displayed video in server memory, so this holds it to one video
per browser session.

Not in here: the processing (pipeline.py) or editing Settings (settings_tab.py).
Called by: app.py.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Callable

import streamlit as st

from .. import assets, hosting, paths, pipeline
from .. import settings as store
from ..compose import ComposeError
from ..downloader import DownloadError
from ..encoder import EncoderError
from ..media_probe import ProbeError
from ..settings import Settings
from .widgets import human_size, reveal

_FAILURES = (DownloadError, pipeline.JobError, ComposeError, ProbeError, EncoderError, OSError)
_LOGIN_HINT = (
    "If this post needs a login (private account, or Instagram asking to log in), "
    "pick your browser under Settings → Instagram login and try again."
)


def render() -> None:
    state = st.session_state
    state.setdefault("results", [])        # list[pipeline.Result], newest first
    state.setdefault("failures", [])       # list[(label, message)]
    state.setdefault("sources_round", 0)   # bumping it empties the uploader

    current = store.load()
    _summary(current)
    links_text = st.text_area(
        "Reel / video links — one per line",
        key="links",
        placeholder="https://www.instagram.com/reel/…",
    )
    uploads = st.file_uploader(
        "…or upload video files", accept_multiple_files=True, key=f"sources_{state.sources_round}"
    ) or []
    links = [line.strip() for line in links_text.splitlines() if line.strip()]
    if st.button("Create videos", type="primary", disabled=not (links or uploads)):
        for link in links:
            _run_item(link, functools.partial(_link_sources, link, current), current, is_link=True)
        for upload in uploads:
            _run_item(upload.name, functools.partial(_upload_sources, upload), current, is_link=False)
        state.sources_round += 1
        st.rerun()
    _results()
    _all_finished(current)


def _summary(current: Settings) -> None:
    def banner(name: str) -> str:
        return "set" if assets.exists(name) else "none"

    shape = "same as the video" if current.frame_aspect == "source" else current.frame_aspect
    parts = [
        f"Frame: **{shape}**",
        f"Top banner: **{banner(current.top_banner)}**",
        f"Bottom banner: **{banner(current.bottom_banner)}**",
        f"Captions: **{sum(bool(t.text.strip()) for t in (current.top_text, current.bottom_text))}**",
    ]
    st.caption(" · ".join(parts) + " — change these in the Settings tab.")


def _link_sources(link: str, current: Settings, report) -> list[tuple[Path, str]]:
    return [(item.path, item.title) for item in pipeline.download_link(link, current, report)]


def _upload_sources(upload, report) -> list[tuple[Path, str]]:
    upload.seek(0)
    return [(pipeline.save_upload(upload.name, upload), Path(upload.name).stem)]


def _run_item(label: str, get_sources: Callable, current: Settings, *, is_link: bool) -> None:
    with st.status(label, expanded=True) as status:
        bar = st.progress(0.0, text="Starting…")

        def report(stage: str, fraction: float | None) -> None:
            text = stage if fraction is None else f"{stage} — {fraction:.0%}"
            bar.progress(fraction or 0.0, text=text)

        try:
            for path, title in get_sources(report):
                result = pipeline.process_file(path, current, report, title=title)
                st.session_state.results.insert(0, result)
        except _FAILURES as exc:
            message = str(exc) + (f"\n\n{_LOGIN_HINT}" if is_link and isinstance(exc, DownloadError) else "")
            st.session_state.failures.append((label, message))
            status.update(label=f"Failed: {label}", state="error")
            st.error(message)
        else:
            status.update(label=f"Done: {label}", state="complete", expanded=False)


def _results() -> None:
    state = st.session_state
    for label, message in state.failures:
        st.error(f"**{label}**\n\n{message}")
    shown = [r for r in state.results if r.output.exists()]
    playing = state.get("playing")
    if shown and playing not in {str(r.output) for r in shown}:
        playing = str(shown[0].output)  # the newest result
    for result in shown:
        with st.container(border=True):
            player, info = st.columns([2, 3])
            with player:
                if str(result.output) == playing:
                    st.video(result.output)
                elif st.button("▶ Play", key=f"play-{result.output}"):
                    state.playing = str(result.output)
                    st.rerun()
            with info:
                st.markdown(f"**{result.title}**")
                st.caption(f"Saved to `{result.output}`")
                st.download_button(
                    "Download MP4",
                    data=functools.partial(Path.read_bytes, result.output),
                    file_name=result.output.name,
                    mime="video/mp4",
                    key=f"download-{result.output}",
                    type="primary",
                    # With the default (rerun on click), Streamlit 1.65 never answered the
                    # deferred-file request in testing and the button spun forever.
                    on_click="ignore",
                )
                if not hosting.hosted():
                    st.button("Show in folder", key=f"reveal-{result.output}",
                              on_click=reveal, args=(result.output,))
    if shown or state.failures:
        if st.button("Clear list"):
            state.results.clear()
            state.failures.clear()
            st.rerun()


def _all_finished(current: Settings) -> None:
    try:
        out_dir = paths.output_dir(current.output_dir)
    except OSError:
        return
    files = sorted((p for p in out_dir.iterdir() if p.is_file() and p.suffix == ".mp4"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return
    with st.expander(f"All finished videos ({len(files)})"):
        pick = st.selectbox("Video", files, key="all_finished_pick",
                            format_func=lambda p: f"{p.name} · {human_size(p.stat().st_size)}")
        st.download_button("Download", data=functools.partial(Path.read_bytes, pick), file_name=pick.name,
                           mime="video/mp4", on_click="ignore", key="all_finished_download")
        if not hosting.hosted():
            st.button("Show in folder", key="all_finished_reveal", on_click=reveal, args=(pick,))
