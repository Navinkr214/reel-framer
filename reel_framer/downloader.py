"""Download a reel / post / video link with yt-dlp.

- Any site yt-dlp supports: Instagram reels and posts, YouTube, Facebook, X,
  plain .mp4 links, ... A post holding several videos (an Instagram carousel)
  gives one file per video. A link to one video inside a playlist downloads
  only that video (noplaylist).
- Format: yt-dlp's own default choice (best video + best audio, merged). The
  merged file is MP4 when the streams fit in MP4, else MKV; ffmpeg reads both.
- Login: Instagram often serves reels only to a logged-in browser. Settings can
  name a browser whose cookies yt-dlp reads, or an uploaded cookies.txt.
- Files are saved as <site>-<id>.<ext> in the given folder, so a link that was
  already downloaded is not fetched again.

Not in here: anything after the file is on disk (pipeline.py).
Called by: pipeline.py, ui/settings_tab.py (browser list, version).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.cookies import SUPPORTED_BROWSERS

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Downloaded:
    path: Path
    title: str
    url: str


def version() -> str:
    return yt_dlp.version.__version__


def supported_browsers() -> list[str]:
    return sorted(SUPPORTED_BROWSERS)


class _QuietLog:
    """yt-dlp logger that prints nothing: errors still arrive as the DownloadError
    yt-dlp raises, and that message is what the user sees."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def download(
    url: str,
    dest: Path,
    *,
    cookies_browser: str = "",
    cookies_file: str = "",
    on_progress: Callable[[float], None] | None = None,
) -> list[Downloaded]:
    def hook(status: dict) -> None:
        if on_progress and status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            done = status.get("downloaded_bytes")
            if total and done is not None:
                on_progress(min(1.0, done / total))

    options = {
        "outtmpl": {"default": str(dest / "%(extractor_key)s-%(id)s.%(ext)s")},
        "merge_output_format": "mp4/mkv",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _QuietLog(),
        "progress_hooks": [hook],
    }
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    if cookies_file:
        options["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError) as exc:
        raise DownloadError(_clean(str(exc))) from exc
    if not info:
        raise DownloadError("yt-dlp found nothing to download at this link.")

    entries = info.get("entries") if info.get("_type") == "playlist" else [info]
    found: list[Downloaded] = []
    for entry in entries or []:
        if not entry:
            continue
        for item in entry.get("requested_downloads") or []:
            path = Path(item.get("filepath") or "")
            if path.is_file():
                title = entry.get("title") or entry.get("id") or path.stem
                found.append(Downloaded(path, str(title), entry.get("webpage_url") or url))
    if not found:
        raise DownloadError("The link has no downloadable video (it may be a photo post).")
    return found


def _clean(message: str) -> str:
    message = _ANSI.sub("", message).strip()
    return message.removeprefix("ERROR:").strip()
