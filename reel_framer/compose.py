"""Build and run the ffmpeg command that draws the final video (or one preview frame).

Filter graph (inputs: 0 = the source, then one per overlay, in Plan order):
  background  "blur":  the source scaled to cover the frame, centre-cropped and
                       blurred (gaussian sigma = Plan.blur_sigma). The blur runs on
                       a small copy with _BLUR_SAMPLES_PER_SIGMA pixels per sigma
                       and is scaled back up: a gaussian's response at that copy's
                       Nyquist frequency is e^(-2*pi^2), about 3e-9, so the copy
                       holds the blurred picture exactly (measured on a real
                       1080x1920 reel: mean PSNR 53 dB against a full-size blur,
                       at a quarter of the CPU time);
              "color": the scaled video padded out with background_color.
  video       the source scaled to layout.video and laid on the background.
  overlays    banners scaled to their rect; captions drawn as rendered. Animated
              inputs (GIF / APNG / clip) are read with -stream_loop -1 so they
              repeat for the whole video, and their overlay ends with the video
              (shortest=1). A still image is one frame, which overlay repeats to
              the end by default (eof_action=repeat); shortest=1 would end the
              video after that one frame.
  output      yuv420p H.264 + the source's first audio track (encoder.py), MP4
              with its index at the front (+faststart) so players start
              before the whole file has arrived.

Threads: when the machine allows fewer CPUs than it shows (a container CPU
quota, see hosting.py), every decoder, the filter graph and the encoder get
Plan.threads threads instead of one per visible core.

Progress: ffmpeg's -progress pipe (out_time_us against the source duration).
ffmpeg's messages go to a log file (nothing piles up in memory); a failed run
raises ComposeError with the first and last error lines and the log's path. If
the caller stops a render (an exception out of on_progress), ffmpeg is killed.

Not in here: sizes (layout.py) or encoder choices (encoder.py).
Called by: pipeline.py.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import ImageColor

from .encoder import OUTPUT_PIX_FMT
from .layout import Layout, Rect, aligned
from .media_probe import MediaInfo

_US_PER_SECOND = 1_000_000  # ffmpeg reports out_time_us in microseconds
_BLUR_SAMPLES_PER_SIGMA = 2   # see "background" above


class ComposeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Overlay:
    path: Path
    rect: Rect          # position; for scaled overlays also the drawn size
    animated: bool      # loop it for the whole video
    scaled: bool        # banners are scaled to rect; captions are drawn as rendered


@dataclass(frozen=True)
class Plan:
    source: Path
    info: MediaInfo
    layout: Layout
    overlays: tuple[Overlay, ...]
    background: str                 # "blur" | "color"
    background_color: str           # "#RRGGBB"
    blur_sigma: float
    video_args: tuple[str, ...]
    audio_args: tuple[str, ...]
    threads: int | None = None      # None: ffmpeg picks (one per visible core)


def filter_graph(plan: Plan, out_format: str = OUTPUT_PIX_FMT) -> str:
    frame, video = plan.layout.frame, plan.layout.video
    scale_video = f"scale={video.w}:{video.h}:flags=lanczos,setsar=1"
    if plan.background == "blur":
        shrink = max(1.0, plan.blur_sigma / _BLUR_SAMPLES_PER_SIGMA)
        small_w, small_h = aligned(frame.w / shrink), aligned(frame.h / shrink)
        parts = [
            "[0:v]split=2[fg][bgsrc]",
            f"[bgsrc]scale={small_w}:{small_h}:force_original_aspect_ratio=increase:flags=area,"
            f"crop={small_w}:{small_h},setsar=1,gblur=sigma={plan.blur_sigma * small_w / frame.w:.3f},"
            # setsar=1: scaling the rounded small copy back up would otherwise change the
            # pixel shape to keep its slightly different aspect, and the video would show squeezed.
            f"scale={frame.w}:{frame.h}:flags=bicubic,setsar=1[bg]",
            f"[fg]{scale_video}[vid]",
            f"[bg][vid]overlay={video.x}:{video.y}[base]",
        ]
    else:
        parts = [f"[0:v]{scale_video},pad={frame.w}:{frame.h}:{video.x}:{video.y}:color={_ffmpeg_color(plan.background_color)}[base]"]
    last = "base"
    for n, overlay in enumerate(plan.overlays, start=1):
        source = f"[{n}:v]"
        if overlay.scaled:
            parts.append(f"{source}scale={overlay.rect.w}:{overlay.rect.h}:flags=lanczos[ov{n}]")
            source = f"[ov{n}]"
        ending = ":shortest=1" if overlay.animated else ""
        parts.append(f"[{last}]{source}overlay={overlay.rect.x}:{overlay.rect.y}{ending}[layer{n}]")
        last = f"layer{n}"
    parts.append(f"[{last}]format={out_format}[out]")
    return ";".join(parts)


def _inputs(plan: Plan, seek: float | None) -> list[str]:
    decoder = ["-threads", str(plan.threads)] if plan.threads else []
    args = [*_graph_threads(plan), *(["-ss", f"{seek:.3f}"] if seek is not None else []),
            *decoder, "-i", str(plan.source)]
    for overlay in plan.overlays:
        args += [*(["-stream_loop", "-1"] if overlay.animated else []), *decoder, "-i", str(overlay.path)]
    return args


def _graph_threads(plan: Plan) -> list[str]:
    return ["-filter_complex_threads", str(plan.threads)] if plan.threads else []


def _encoder_threads(plan: Plan) -> list[str]:
    return ["-threads", str(plan.threads)] if plan.threads else []


def video_command(plan: Plan, output: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error",
        *_inputs(plan, None),
        "-filter_complex", filter_graph(plan),
        "-map", "[out]", "-map", "0:a:0?",
        *plan.video_args, *_encoder_threads(plan), *plan.audio_args,
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(output),
    ]


def frame_command(plan: Plan, output: Path, at: float) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error",
        *_inputs(plan, at),
        "-filter_complex", filter_graph(plan, "rgb24"),  # a PNG still: full-resolution colour
        "-map", "[out]", "-frames:v", "1", "-update", "1",
        str(output),
    ]


def run_video(plan: Plan, output: Path, log_path: Path, on_progress: Callable[[float], None] | None = None) -> None:
    duration = plan.info.duration
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(video_command(plan, output), stdout=subprocess.PIPE, stderr=log, text=True)
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit() and duration and on_progress:
                    on_progress(min(1.0, int(value) / _US_PER_SECOND / duration))
            code = proc.wait()
        except BaseException:
            # The caller gave up (e.g. the browser tab closed and Streamlit stopped the
            # script inside on_progress): never leave ffmpeg running on its own.
            proc.kill()
            proc.wait()
            raise
    if code != 0:
        raise ComposeError(_failure(log_path, code))


def run_frame(plan: Plan, output: Path, at: float) -> None:
    proc = subprocess.run(frame_command(plan, output, at), capture_output=True, text=True)
    if proc.returncode != 0 or not output.exists():
        raise ComposeError(f"ffmpeg could not draw the preview: {proc.stderr.strip() or proc.returncode}")


def _failure(log_path: Path, code: int) -> str:
    lines = [ln.strip() for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    if not lines:
        return f"ffmpeg stopped with exit code {code} (log: {log_path})."
    detail = lines[0] if len(lines) == 1 else f"{lines[0]} ... {lines[-1]}"
    return f"ffmpeg failed: {detail} (full log: {log_path})"


def _ffmpeg_color(value: str) -> str:
    r, g, b = ImageColor.getrgb(value)[:3]
    return f"0x{r:02X}{g:02X}{b:02X}"
