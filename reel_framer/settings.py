"""The saved settings: frame, banners, captions, output and download options.

Values that can be derived are stored as "derive" markers, not numbers:
- frame_aspect "source"  -> the downloaded video's own aspect ratio
- frame_short_side 0     -> the downloaded video's own short side (keeps its resolution)
- quality "match"        -> the source's own bitrate per pixel (encoder.py)
- caption font ""        -> the operating system's UI font, with the OS's own
                            per-script fallback (text/backend.py)
The rest are look-and-feel choices the user owns in the Settings tab; the
defaults below are only starting points, and every one is editable there.

Saved as JSON at paths.settings_file(). Loading keeps each known field whose
value has the right type (and, for choice fields, a known choice) and falls
back to the default for anything missing or malformed, so settings files from
older or newer versions still load.

Not in here: storing uploaded files (assets.py) or using the values (pipeline.py).
Called by: ui/settings_tab.py, ui/create_tab.py, pipeline.py, cli.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

PLACEMENTS = ("over_banner", "band", "over_video")
ALIGNS = ("center", "left", "right")
STACKS = ("edges", "hug")
BACKGROUNDS = ("blur", "color")
QUALITIES = ("match", "crf")

_CHOICES = {
    "placement": PLACEMENTS,
    "align": ALIGNS,
    "stack": STACKS,
    "background": BACKGROUNDS,
    "quality": QUALITIES,
}


@dataclass
class TextStyle:
    text: str = ""
    # over_banner: centred on the banner (its own band when there is no banner);
    # band: a strip between the banner and the video; over_video: on the video's edge.
    placement: str = "over_banner"
    font: str = ""            # "" = OS UI font; "ps:<PostScript name>"; "asset:<file>" (uploaded)
    bold: bool = True         # weight of the OS UI font (only when font == "")
    size_pct: float = 6.0     # font size, % of the frame width
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_pct: float = 8.0  # outline thickness, % of the font size (0 = none)
    box_color: str = "#000000"
    box_opacity_pct: float = 0.0  # box behind the text (0 = no box)
    align: str = "center"
    line_spacing: float = 1.0  # x the font's own line height


@dataclass
class Settings:
    # Frame (the output picture)
    frame_aspect: str = "source"   # "source" or "W:H", e.g. "9:16"
    frame_short_side: int = 0      # px; 0 = the source's own short side
    # Banners: stored asset file names ("" = none)
    top_banner: str = ""
    bottom_banner: str = ""
    banner_max_pct: float = 25.0   # each banner / caption band: at most this % of the frame height
    stack: str = "edges"           # edges: banners at the frame edges; hug: banners touch the video
    # Video
    video_scale_pct: float = 100.0  # video size within the room the banners leave
    background: str = "blur"        # blur: blurred copy of the video; color: background_color
    background_color: str = "#000000"
    blur_pct: float = 3.0           # blur strength (gaussian sigma), % of the frame width
    # Captions
    top_text: TextStyle = field(default_factory=TextStyle)
    bottom_text: TextStyle = field(default_factory=TextStyle)
    margin_pct: float = 4.0         # caption side margin, % of the frame width
    # Output
    quality: str = "match"          # match: the source's bitrate per pixel; crf: constant quality
    crf: int = 18                   # ffmpeg's H.264 guide: 17-18 is "visually lossless or nearly so"
    encoder_preset: str = ""        # x264 speed preset ("" = the encoder's default); faster = less CPU
    output_dir: str = ""            # "" = <home>/output
    # Downloads
    cookies_browser: str = ""       # browser whose login cookies yt-dlp reads ("" = none)
    cookies_file: str = ""          # uploaded cookies.txt asset ("" = none)


def load(path: Path | None = None) -> Settings:
    path = path or paths.settings_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return _merge(Settings(), raw if isinstance(raw, dict) else {})


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or paths.settings_file()
    text = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
    # Write then rename, so a crash never leaves a half-written settings file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def to_json(settings: Settings) -> str:
    """Stable text form, used as a cache key for previews."""
    return json.dumps(asdict(settings), ensure_ascii=False, sort_keys=True)


def from_json(text: str) -> Settings:
    return _merge(Settings(), json.loads(text))


def _merge(target: Any, raw: dict) -> Any:
    for f in fields(target):
        if f.name not in raw:
            continue
        current, value = getattr(target, f.name), raw[f.name]
        if isinstance(current, TextStyle):
            if isinstance(value, dict):
                _merge(current, value)
        elif isinstance(current, bool):
            if isinstance(value, bool):
                setattr(target, f.name, value)
        elif isinstance(current, (int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(target, f.name, type(current)(value))
        elif isinstance(current, str) and isinstance(value, str):
            if f.name not in _CHOICES or value in _CHOICES[f.name]:
                setattr(target, f.name, value)
    return target
