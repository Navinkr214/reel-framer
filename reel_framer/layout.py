"""Pure geometry: the output frame, and where the banners, caption bands and
video sit inside it. No I/O; every number derives from the source size, the
banner sizes, the caption sizes and the Settings.

Rules
- Frame: Settings.frame_aspect ("source" = the source's own aspect ratio) at
  Settings.frame_short_side (0 = the source's own short side).
- Banner: the full frame width at the banner's own aspect ratio. If that is
  taller than banner_max_pct of the frame height, it is shrunk (aspect kept)
  and centred.
- Caption band: a full-width strip, as tall as the caption needs.
- Video: fitted (aspect kept) into the room the top and bottom blocks leave,
  times video_scale_pct, centred horizontally.
    stack "edges": top blocks at the top edge, bottom blocks at the bottom
                   edge, video centred in between;
    stack "hug":   the blocks touch the video and the whole stack is centred.
  Order top to bottom: top banner, top band, video, bottom band, bottom banner.
- Sizes and offsets are multiples of CHROMA_ALIGN (encoder.py).

Not in here: measuring captions (text/render.py) or drawing (compose.py).
Called by: pipeline.py, tests.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .encoder import CHROMA_ALIGN


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Size:
    w: int
    h: int


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass(frozen=True)
class Layout:
    frame: Size
    video: Rect
    top_banner: Rect | None = None
    bottom_banner: Rect | None = None
    top_band: Rect | None = None
    bottom_band: Rect | None = None


def aligned(value: float) -> int:
    """Nearest multiple of CHROMA_ALIGN, never below one step."""
    return max(CHROMA_ALIGN, int(round(value / CHROMA_ALIGN)) * CHROMA_ALIGN)


def aligned_down(value: float) -> int:
    return math.floor(value / CHROMA_ALIGN) * CHROMA_ALIGN


def parse_aspect(text: str) -> tuple[int, int] | None:
    """"source" -> None; "9:16" -> (9, 16)."""
    if text == "source":
        return None
    w, sep, h = text.partition(":")
    if sep and w.strip().isdigit() and h.strip().isdigit() and int(w) > 0 and int(h) > 0:
        return int(w), int(h)
    raise LayoutError(f"Frame shape {text!r} is not 'source' or W:H (for example 9:16).")


def frame_size(source: Size, aspect: str, short_side: int) -> Size:
    ratio_w, ratio_h = parse_aspect(aspect) or (source.w, source.h)
    short = short_side if short_side > 0 else min(source.w, source.h)
    if ratio_w <= ratio_h:
        return Size(aligned(short), aligned(short * ratio_h / ratio_w))
    return Size(aligned(short * ratio_w / ratio_h), aligned(short))


def banner_size(frame: Size, banner: Size, max_h: float) -> Size:
    w, h = float(frame.w), frame.w * banner.h / banner.w
    if h > max_h:
        w, h = w * max_h / h, max_h
    return Size(min(frame.w, aligned(w)), aligned(h))


def arrange(
    frame: Size,
    source: Size,
    *,
    top_banner: Size | None = None,
    bottom_banner: Size | None = None,
    top_band_h: int = 0,
    bottom_band_h: int = 0,
    banner_max_pct: float,
    video_scale_pct: float,
    stack: str,
) -> Layout:
    max_h = frame.h * banner_max_pct / 100
    top = banner_size(frame, top_banner, max_h) if top_banner else None
    bottom = banner_size(frame, bottom_banner, max_h) if bottom_banner else None
    top_h = (top.h if top else 0) + top_band_h
    bottom_h = (bottom.h if bottom else 0) + bottom_band_h
    room = frame.h - top_h - bottom_h
    if room < CHROMA_ALIGN:
        raise LayoutError(
            f"The banners and caption bands take {top_h + bottom_h}px of the {frame.h}px "
            "frame height, leaving no room for the video. Lower 'Banner max height' or the "
            "caption size in Settings."
        )

    fit = min(frame.w / source.w, room / source.h) * video_scale_pct / 100
    video_w = min(aligned(source.w * fit), frame.w)
    video_h = min(aligned(source.h * fit), aligned_down(room))
    video_x = aligned_down((frame.w - video_w) / 2)

    y = aligned_down((frame.h - top_h - video_h - bottom_h) / 2) if stack == "hug" else 0
    top_rect = Rect(aligned_down((frame.w - top.w) / 2), y, top.w, top.h) if top else None
    top_band = Rect(0, y + (top.h if top else 0), frame.w, top_band_h) if top_band_h else None
    if stack == "hug":
        video_y = y + top_h
        bottom_y = video_y + video_h
    else:
        video_y = top_h + aligned_down((room - video_h) / 2)
        bottom_y = frame.h - bottom_h
    bottom_band = Rect(0, bottom_y, frame.w, bottom_band_h) if bottom_band_h else None
    bottom_rect = (
        Rect(aligned_down((frame.w - bottom.w) / 2), bottom_y + bottom_band_h, bottom.w, bottom.h)
        if bottom else None
    )
    return Layout(
        frame=frame,
        video=Rect(video_x, video_y, video_w, video_h),
        top_banner=top_rect,
        bottom_banner=bottom_rect,
        top_band=top_band,
        bottom_band=bottom_band,
    )


def caption_position(zone: Rect, caption: Size, align: str, margin: int, anchor: str) -> tuple[int, int]:
    """Top-left corner for a caption inside `zone`.

    align: left / center / right (with `margin` from the side);
    anchor: "center" (vertically centred), "top" or "bottom" (`margin` from that edge).
    """
    if align == "left":
        x = zone.x + margin
    elif align == "right":
        x = zone.right - margin - caption.w
    else:
        x = zone.x + (zone.w - caption.w) / 2
    if anchor == "top":
        y = zone.y + margin
    elif anchor == "bottom":
        y = zone.bottom - margin - caption.h
    else:
        y = zone.y + (zone.h - caption.h) / 2
    return aligned_down(x), aligned_down(y)
