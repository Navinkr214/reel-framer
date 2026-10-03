"""One job end to end: a link or a file -> a framed MP4 (or a preview frame).

download_link(url, settings) fetches the link's video(s) (downloader.py).
process_file(source, settings) then:
 1. probes the source, and the banners with their frames counted, so the
    animated ones (GIF / APNG / clip) are looped for the whole video;
 2. sizes the frame (layout.frame_size);
 3. renders captions that get their own band, across the frame width;
 4. arranges banners, bands and video (layout.arrange);
 5. renders captions that sit on a banner or on the video, fitted to it;
 6. runs ffmpeg (compose.py), reporting progress.
preview(source, settings) runs the same steps for one frame and returns PNG bytes.

Derived values: the caption margin is Settings.margin_pct of the frame width;
a caption band is the caption's height plus that margin above and below, and
may take at most banner_max_pct of the frame height, like a banner. The blur
sigma is blur_pct of the frame width. ffmpeg's thread count follows the CPUs
the machine really allows (hosting.thread_limit). The preview frame defaults to
the middle of the video.

Renders run one at a time per app process (_RENDER_SLOT); a job that arrives
while another renders reports "Waiting for the video ahead to finish".

Caption images go to a per-job folder under paths.work_dir(), removed when the
job ends whatever the outcome; a failed render also removes its partial MP4.

Not in here: the UI (ui/*) or the command line (cli.py).
Called by: ui/create_tab.py, ui/settings_tab.py, cli.py, tests.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

import regex

from . import assets, compose, downloader, encoder, hosting, layout, paths
from .layout import Rect, Size
from .media_probe import MediaInfo, probe
from .settings import Settings, TextStyle
from .text.render import Caption, CaptionError, render

Report = Callable[[str, float | None], None]

_SIDES = ("top", "bottom")
# One render at a time per app process: each render already uses every CPU the
# machine allows (encoder and filter threads), so two at once only split the CPU
# and double the memory, which is what runs a small server out of RAM.
_RENDER_SLOT = threading.Lock()
# Hash bytes for naming an uploaded source; see assets._HASH_BYTES.
_HASH_BYTES = 8


class JobError(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    title: str
    output: Path
    source: Path


def _quiet(stage: str, fraction: float | None) -> None:
    pass


def download_link(url: str, settings: Settings, report: Report = _quiet) -> list[downloader.Downloaded]:
    """Login cookies: an uploaded cookies.txt, else the host's (hosting.host_cookies_file)."""
    if assets.exists(settings.cookies_file):
        cookies = str(assets.path_of(settings.cookies_file))
    else:
        host_file = hosting.host_cookies_file()
        cookies = str(host_file) if host_file else ""
    return downloader.download(
        url.strip(),
        paths.downloads_dir(),
        cookies_browser=settings.cookies_browser,
        cookies_file=cookies,
        on_progress=lambda fraction: report("Downloading", fraction),
    )


def save_upload(name: str, stream: BinaryIO) -> Path:
    """Keep an uploaded source video next to the downloads (for previews and re-renders).

    Copied in buffer-sized pieces while hashing, so a big upload is never held twice in memory.
    """
    stem = regex.sub(r"[^\p{L}\p{N}_-]+", "-", Path(name).stem).strip("-") or "upload"
    digest = hashlib.blake2s(digest_size=_HASH_BYTES)
    fd, tmp_name = tempfile.mkstemp(dir=paths.downloads_dir(), prefix=".upload-", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            for chunk in iter(lambda: stream.read(io.DEFAULT_BUFFER_SIZE), b""):
                digest.update(chunk)
                out.write(chunk)
        target = paths.downloads_dir() / f"upload-{stem}-{digest.hexdigest()}{Path(name).suffix.lower()}"
        if target.exists():
            tmp.unlink()
        else:
            os.replace(tmp, target)
        return target
    finally:
        tmp.unlink(missing_ok=True)


def recent_sources() -> list[Path]:
    """Downloaded / uploaded sources, newest first."""
    files = [p for p in paths.downloads_dir().iterdir() if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def process_file(source: Path, settings: Settings, report: Report = _quiet, *, title: str = "") -> Result:
    job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=paths.work_dir()))
    try:
        report("Preparing", None)
        plan = build_plan(source, settings, job_dir)
        out_dir = paths.output_dir(settings.output_dir)
        output = out_dir / _output_name(source.stem, out_dir)
        log = paths.logs_dir() / f"{output.stem}.log"
        if not _RENDER_SLOT.acquire(blocking=False):
            report("Waiting for the video ahead to finish", None)
            _RENDER_SLOT.acquire()
        try:
            compose.run_video(plan, output, log, lambda fraction: report("Rendering", fraction))
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        finally:
            _RENDER_SLOT.release()
        log.unlink(missing_ok=True)
        report("Done", 1.0)
        return Result(title or source.stem, output, source)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def preview(source: Path, settings: Settings, at: float | None = None) -> bytes:
    job_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=paths.work_dir()))
    try:
        plan = build_plan(source, settings, job_dir)
        when = at if at is not None else (plan.info.duration or 0) / 2
        out = job_dir / "preview.png"
        compose.run_frame(plan, out, when)
        return out.read_bytes()
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def build_plan(source: Path, settings: Settings, job_dir: Path) -> compose.Plan:
    info = probe(source, check_animated=True)
    if not info.animated:
        raise JobError(f"{source.name} is a still image, not a video. "
                       "To show a picture on every frame, add it as a banner in Settings.")
    src = Size(info.width, info.height)
    frame = layout.frame_size(src, settings.frame_aspect, settings.frame_short_side)
    margin = round(frame.w * settings.margin_pct / 100)

    banners: dict[str, tuple[Path, MediaInfo]] = {}
    for side, name in zip(_SIDES, (settings.top_banner, settings.bottom_banner)):
        if not name:
            continue
        if not assets.exists(name):
            raise JobError(f"The {side} banner file is missing. Upload it again in Settings.")
        path = assets.path_of(name)
        banners[side] = (path, probe(path, check_animated=True))

    styles = dict(zip(_SIDES, (settings.top_text, settings.bottom_text)))
    placements = {
        side: ("band" if style.placement == "over_banner" and side not in banners else style.placement)
        for side, style in styles.items()
    }
    band_room = frame.h * settings.banner_max_pct / 100 - 2 * margin
    band_captions: dict[str, Caption] = {}
    for side, style in styles.items():
        if placements[side] == "band":
            caption = _caption(side, style, frame.w, frame.w - 2 * margin, band_room)
            if caption:
                band_captions[side] = caption

    def band_h(side: str) -> int:
        caption = band_captions.get(side)
        return layout.aligned(caption.image.height + 2 * margin) if caption else 0

    def banner_size(side: str) -> Size | None:
        return Size(banners[side][1].width, banners[side][1].height) if side in banners else None

    try:
        plan_layout = layout.arrange(
            frame, src,
            top_banner=banner_size("top"), bottom_banner=banner_size("bottom"),
            top_band_h=band_h("top"), bottom_band_h=band_h("bottom"),
            banner_max_pct=settings.banner_max_pct,
            video_scale_pct=settings.video_scale_pct,
            stack=settings.stack,
        )
    except layout.LayoutError as exc:
        raise JobError(str(exc)) from exc

    overlays: list[compose.Overlay] = []
    for side in _SIDES:
        if side in banners:
            path, banner_info = banners[side]
            rect = plan_layout.top_banner if side == "top" else plan_layout.bottom_banner
            overlays.append(compose.Overlay(path, rect, banner_info.animated, scaled=True))

    for side, style in styles.items():
        placement = placements[side]
        if placement == "band":
            caption = band_captions.get(side)
            zone = plan_layout.top_band if side == "top" else plan_layout.bottom_band
            anchor = "center"
        elif placement == "over_banner":
            zone = plan_layout.top_banner if side == "top" else plan_layout.bottom_banner
            caption = _caption(side, style, frame.w, zone.w - 2 * margin, zone.h)
            anchor = "center"
        else:  # over_video
            zone = plan_layout.video
            caption = _caption(side, style, frame.w, zone.w - 2 * margin, zone.h - 2 * margin)
            anchor = side
        if caption is None or zone is None:
            continue
        png = job_dir / f"{side}-caption.png"
        caption.image.save(png)
        size = Size(caption.image.width, caption.image.height)
        x, y = layout.caption_position(zone, size, style.align, margin, anchor)
        overlays.append(compose.Overlay(png, Rect(x, y, size.w, size.h), animated=False, scaled=False))

    return compose.Plan(
        source=source,
        info=info,
        layout=plan_layout,
        overlays=tuple(overlays),
        background=settings.background,
        background_color=settings.background_color,
        blur_sigma=frame.w * settings.blur_pct / 100,
        video_args=tuple(encoder.video_args(info, frame.w, frame.h, settings.quality, settings.crf,
                                            settings.encoder_preset)),
        audio_args=tuple(encoder.audio_args(info)),
        threads=hosting.thread_limit(),
    )


def _caption(side: str, style: TextStyle, frame_w: int, max_w: float, max_h: float) -> Caption | None:
    try:
        return render(style.text, style, frame_w, max_w, max_h)
    except CaptionError as exc:
        raise JobError(f"The {side} caption cannot be drawn: {exc}") from exc


def _output_name(stem: str, out_dir: Path) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-framed-{stamp}.mp4"
    # Keep the whole name within the file system's own limit on name length.
    limit = os.pathconf(out_dir, "PC_NAME_MAX") - len(suffix.encode())
    raw = stem.encode()[:max(0, limit)].decode(errors="ignore")
    name = f"{raw}{suffix}"
    counter = 1
    while (out_dir / name).exists():  # two renders of one source in the same second
        counter += 1
        name = f"{raw}{suffix[:-len('.mp4')]}-{counter}.mp4"
    return name
