"""Output encoding arguments, derived from the live ffmpeg build and the source.

Format: MP4 with H.264 video in yuv420p and AAC audio, the upload format that
Instagram, Facebook, YouTube Shorts and WhatsApp all accept. The H.264 encoder
is whatever this ffmpeg build resolves `h264` to (`ffmpeg -h encoder=h264`):
libx264 when it is compiled in, else the build's other H.264 encoder.

yuv420p stores colour at half resolution both ways (2x2 chroma subsampling),
so frame sizes and overlay offsets must be multiples of CHROMA_ALIGN; layout.py
aligns everything to it.

Video bitrate (Settings.quality):
- "match": the source's bits per pixel carried over to the output frame, never
  below the source's own bitrate, so the re-encode spends at least what the
  source spent on the same picture. If the source reports no bitrate at all,
  the encoder's own default rate control is used.
- "crf": constant quality Settings.crf, when the encoder lists a -crf option
  in `ffmpeg -h encoder=...`; otherwise "match" is used.
Speed (Settings.encoder_preset): one of libx264/libx265's own preset names,
offered only when the build's H.264 encoder is one of those; "" keeps the
encoder's default. Faster presets spend less CPU per frame for a slightly
bigger file, which matters most on small servers.
Audio: copied when the source is already AAC; otherwise AAC at the source's own
bitrate (the encoder's default when the source reports none).

Not in here: the filter graph (compose.py).
Called by: compose.py, layout.py (CHROMA_ALIGN), ui/settings_tab.py, tests.
"""
from __future__ import annotations

import functools
import re
import subprocess

from .media_probe import MediaInfo

OUTPUT_VIDEO_CODEC = "h264"
OUTPUT_AUDIO_CODEC = "aac"
OUTPUT_PIX_FMT = "yuv420p"
CHROMA_ALIGN = 2  # yuv420p's 2x2 chroma subsampling
# libx264 / libx265 speed presets, fastest first: the closed vocabulary of those two
# encoders (x264 --fullhelp); ffmpeg cannot list them.
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
_X264_FAMILY = ("libx264", "libx265")


class EncoderError(RuntimeError):
    pass


@functools.cache
def _encoder_help(name: str) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", f"encoder={name}"], capture_output=True, text=True
    )
    return proc.stdout


def encoder_name(codec: str = OUTPUT_VIDEO_CODEC) -> str:
    """The encoder this ffmpeg build uses for `codec`."""
    match = re.match(r"Encoder (\S+)", _encoder_help(codec).strip())
    if not match:
        raise EncoderError(f"This ffmpeg build has no {codec} encoder.")
    return match.group(1)


def encoder_options(name: str) -> frozenset[str]:
    return frozenset(re.findall(r"^\s+-(\S+)", _encoder_help(name), re.MULTILINE))


def presets() -> tuple[str, ...]:
    """Speed presets this build's H.264 encoder accepts; () when it is not x264/x265."""
    return X264_PRESETS if encoder_name() in _X264_FAMILY else ()


def target_bitrate(source: MediaInfo, frame_w: int, frame_h: int) -> int | None:
    """Source bits per pixel x output pixels, floored at the source bitrate."""
    bps = source.video_bitrate
    if not bps and source.container_bitrate:
        bps = source.container_bitrate - (source.audio_bitrate or 0)
    if not bps or bps <= 0:
        return None
    pixel_ratio = (frame_w * frame_h) / (source.width * source.height)
    return round(bps * max(1.0, pixel_ratio))


def video_args(source: MediaInfo, frame_w: int, frame_h: int, quality: str, crf: int,
               preset: str = "") -> list[str]:
    name = encoder_name()
    args = ["-c:v", name, "-pix_fmt", OUTPUT_PIX_FMT]
    if preset and preset in presets():
        args += ["-preset", preset]
    if quality == "crf" and "crf" in encoder_options(name):
        return args + ["-crf", str(crf)]
    bitrate = target_bitrate(source, frame_w, frame_h)
    return args + (["-b:v", str(bitrate)] if bitrate else [])


def audio_args(source: MediaInfo) -> list[str]:
    if not source.has_audio:
        return []
    if source.audio_codec == OUTPUT_AUDIO_CODEC:
        return ["-c:a", "copy"]
    args = ["-c:a", OUTPUT_AUDIO_CODEC]
    return args + (["-b:a", str(source.audio_bitrate)] if source.audio_bitrate else [])
