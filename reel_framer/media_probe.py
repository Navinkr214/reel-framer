"""ffprobe wrapper: what a video, image or GIF actually is.

MediaInfo carries the DISPLAY size (what a player shows): the coded size with
the sample aspect ratio applied, and width/height swapped when the stream
carries a 90/270-degree display rotation (phone footage). ffmpeg applies both
when it decodes, so the layout must plan with the display size.

`animated` is checked only on request (check_animated=True): it tells a still
image from an animated GIF / clip / video. A second ffprobe reads at most two
packets OF THE CHOSEN VIDEO STREAM (two is the fewest that tell one frame from
many), so the check costs the same for a sticker and for an hour of video. The
packet limit must be applied to that stream alone: downloads often start with
audio packets, and a limit over all streams then sees no video at all.

Not in here: deciding what to do with these facts (layout.py, assets.py, encoder.py).
Called by: assets.py, pipeline.py, encoder.py, tests.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    width: int                      # display width
    height: int                     # display height
    fps: Fraction | None            # average frame rate, when the stream reports one
    duration: float | None          # seconds
    video_codec: str
    video_bitrate: int | None       # bits per second
    audio_codec: str | None         # None = no audio stream
    audio_bitrate: int | None
    container_bitrate: int | None   # whole-file bitrate (format.bit_rate)
    animated: bool | None           # more than one frame; None = not checked

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


_ANIMATION_PROBE_PACKETS = 2  # one frame vs more than one


def probe(path: Path | str, *, check_animated: bool = False) -> MediaInfo:
    path = Path(path)
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe could not read {path.name}: {proc.stderr.strip() or 'unknown error'}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next(
        (s for s in streams
         if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    if video is None or not video.get("width") or not video.get("height"):
        raise ProbeError(f"{path.name} has no video or image stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    width, height = int(video["width"]), int(video["height"])
    sar = _ratio(video.get("sample_aspect_ratio"))
    if sar is not None and sar != 1:
        width = round(width * sar)
    if _rotation(video) % 180 == 90:
        width, height = height, width

    return MediaInfo(
        width=width,
        height=height,
        fps=_ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")),
        duration=_float(fmt.get("duration")) or _float(video.get("duration")),
        video_codec=video.get("codec_name", ""),
        video_bitrate=_int(video.get("bit_rate")),
        audio_codec=audio.get("codec_name") if audio else None,
        audio_bitrate=_int(audio.get("bit_rate")) if audio else None,
        container_bitrate=_int(fmt.get("bit_rate")),
        animated=_has_motion(path, video["index"]) if check_animated else None,
    )


def _has_motion(path: Path, stream_index: int) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", str(stream_index),
         "-read_intervals", f"%+#{_ANIMATION_PROBE_PACKETS}", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return (_int(proc.stdout.strip().split(",")[0]) or 0) >= _ANIMATION_PROBE_PACKETS


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            return int(round(float(side["rotation"])))
    rotate = stream.get("tags", {}).get("rotate")
    return int(rotate) if rotate and rotate.lstrip("-").isdigit() else 0


def _ratio(text: str | None) -> Fraction | None:
    if not text:
        return None
    sep = ":" if ":" in text else "/"
    num, _, den = text.partition(sep)
    try:
        num_i, den_i = int(num), int(den or 1)
    except ValueError:
        return None
    return Fraction(num_i, den_i) if num_i > 0 and den_i > 0 else None


def _float(text) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _int(text) -> int | None:
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
