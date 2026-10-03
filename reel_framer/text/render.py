"""Render a caption to a transparent RGBA image: per-script font fallback,
wrapping to a width, outline, optional rounded box, shrink-to-fit.

Sizes derive from the frame and from the faces in use:
- font size: TextStyle.size_pct of the frame width;
- outline: TextStyle.outline_pct of the font size;
- box padding and corner radius: the base face's descent, a unit of the face
  itself, so they scale with the font;
- line height: the faces' own ascent + descent (grown where a glyph's ink
  reaches past them, e.g. stacked marks or emoji), times line_spacing.

Wrapping breaks at whitespace. A word wider than the line breaks between
grapheme clusters, so conjuncts, emoji sequences and combining marks stay
whole, and scripts written without spaces wrap by cluster. Explicit line
breaks in the text are kept.

Fit: if the image is wider than max_w or taller than max_h, the font size
shrinks in proportion to the overflow (by at least one pixel per step) until
it fits. Below one pixel it raises CaptionError.

Not in here: choosing faces (backend.py) or placing the caption (layout.py).
Called by: pipeline.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import regex
from PIL import Image, ImageColor, ImageDraw

from ..settings import TextStyle
from . import backend
from .faces import Base, Face, LoadedFont, load

_ALPHA_MAX = 255  # 8-bit alpha channel


class CaptionError(ValueError):
    pass


@dataclass(frozen=True)
class Caption:
    image: Image.Image
    font_px: float


@dataclass
class _Piece:
    text: str
    font: LoadedFont
    x: float       # advance offset inside the line
    width: float   # advance width


@dataclass
class _Line:
    pieces: list[_Piece] = field(default_factory=list)
    width: float = 0.0       # advance width
    ascent: float = 0.0
    descent: float = 0.0
    ink_left: float = 0.0    # leftmost ink, relative to the line start (negative = overhang)
    ink_right: float = 0.0   # rightmost ink


def render(text: str, style: TextStyle, frame_w: int, max_w: float, max_h: float | None = None) -> Caption | None:
    """The caption image, or None when the text is blank."""
    if not text.strip():
        return None
    if max_w <= 0 or (max_h is not None and max_h <= 0):
        raise CaptionError(f"there is no room for it ({max_w:.0f} x {max_h or 0:.0f} px).")
    base = backend.base_from_setting(style.font, style.bold)
    px = frame_w * style.size_pct / 100
    while True:
        image = _draw(text, style, base, px, max_w)
        overflow = max(image.width / max_w, image.height / max_h if max_h else 0.0)
        if overflow <= 1:
            return Caption(image, px)
        smaller = min(math.floor(px / overflow), math.ceil(px) - 1)
        if smaller < 1:
            raise CaptionError(
                f"it does not fit in {max_w:.0f} x {max_h or 0:.0f} px even at a 1 px font size."
            )
        px = smaller


def _draw(text: str, style: TextStyle, base: Base, px: float, max_w: float) -> Image.Image:
    fonts: dict[Face, LoadedFont] = {}

    def font_of(face: Face) -> LoadedFont:
        if face not in fonts:
            fonts[face] = load(face, px)
        return fonts[face]

    base_font = font_of(backend.face_for(base, px))
    base_ascent, base_descent = (m * base_font.scale for m in base_font.font.getmetrics())
    stroke = round(px * style.outline_pct / 100)
    boxed = style.box_opacity_pct > 0
    pad = base_descent if boxed else 0.0
    edge = stroke + pad
    avail = max_w - 2 * edge

    lines: list[_Line] = []
    for paragraph in text.strip("\n").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append(_Line(ascent=base_ascent, descent=base_descent))
            continue
        spans = backend.segment(paragraph, base, px)
        for start, end in _wrap(paragraph, spans, avail, font_of):
            lines.append(_line(paragraph, spans, start, end, font_of))

    content_w = max(line.width for line in lines)
    offsets = [_align_offset(style.align, content_w, line.width) for line in lines]
    extra_left = max(0.0, max(-(o + line.ink_left) for o, line in zip(offsets, lines)))
    extra_right = max(0.0, max(o + line.ink_right - content_w for o, line in zip(offsets, lines)))
    heights = [(line.ascent + line.descent) * (style.line_spacing if i < len(lines) - 1 else 1.0)
               for i, line in enumerate(lines)]
    width = math.ceil(content_w + extra_left + extra_right + 2 * edge)
    height = math.ceil(sum(heights) + 2 * edge)

    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if boxed:
        r, g, b = ImageColor.getrgb(style.box_color)[:3]
        alpha = round(_ALPHA_MAX * style.box_opacity_pct / 100)
        draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=pad, fill=(r, g, b, alpha))
    fill = ImageColor.getrgb(style.color)[:3] + (_ALPHA_MAX,)
    outline = ImageColor.getrgb(style.outline_color)[:3] + (_ALPHA_MAX,)

    y = edge
    for line, offset, line_h in zip(lines, offsets, heights):
        baseline = y + line.ascent
        x0 = edge + extra_left + offset
        for piece in line.pieces:
            if piece.font.scale == 1.0:
                draw.text(
                    (x0 + piece.x, baseline), piece.text, font=piece.font.font, anchor="ls",
                    fill=fill, embedded_color=piece.font.color,
                    stroke_width=0 if piece.font.color else stroke, stroke_fill=outline,
                )
            else:
                _paste_scaled(image, piece, x0 + piece.x, baseline)
        y += line_h
    return image


def _align_offset(align: str, content_w: float, line_w: float) -> float:
    if align == "left":
        return 0.0
    if align == "right":
        return content_w - line_w
    return (content_w - line_w) / 2


def _measure(paragraph: str, spans, start: int, end: int, font_of) -> float:
    total = 0.0
    for s, e, face in spans:
        lo, hi = max(start, s), min(end, e)
        if lo < hi:
            loaded = font_of(face)
            total += loaded.font.getlength(paragraph[lo:hi]) * loaded.scale
    return total


def _wrap(paragraph: str, spans, avail: float, font_of) -> list[tuple[int, int]]:
    """Greedy line breaking: [(start, end)] index ranges of `paragraph`."""
    lines: list[tuple[int, int]] = []
    current: list[int] | None = None  # [start, end] of the line being filled

    def place(start: int, end: int) -> None:
        nonlocal current
        if current and _measure(paragraph, spans, current[0], end, font_of) <= avail:
            current[1] = end
            return
        if current:
            lines.append((current[0], current[1]))
        current = [start, end]

    for word in regex.finditer(r"\S+", paragraph):
        if _measure(paragraph, spans, word.start(), word.end(), font_of) <= avail:
            place(word.start(), word.end())
            continue
        # Wider than a whole line: break it between grapheme clusters.
        for i, cluster in enumerate(regex.finditer(r"\X", word.group())):
            start, end = word.start() + cluster.start(), word.start() + cluster.end()
            if i == 0 or current is None:
                place(start, end)
            elif _measure(paragraph, spans, current[0], end, font_of) <= avail:
                current[1] = end
            else:
                lines.append((current[0], current[1]))
                current = [start, end]
    if current:
        lines.append((current[0], current[1]))
    return lines


def _line(paragraph: str, spans, start: int, end: int, font_of) -> _Line:
    line = _Line()
    x = 0.0
    for s, e, face in spans:
        lo, hi = max(start, s), min(end, e)
        if lo >= hi:
            continue
        loaded, text = font_of(face), paragraph[lo:hi]
        scale = loaded.scale
        width = loaded.font.getlength(text) * scale
        ascent, descent = loaded.font.getmetrics()
        left, top, right, bottom = loaded.font.getbbox(text, anchor="ls")
        line.ascent = max(line.ascent, ascent * scale, -top * scale)
        line.descent = max(line.descent, descent * scale, bottom * scale)
        line.ink_left = min(line.ink_left, x + left * scale)
        line.ink_right = max(line.ink_right, x + right * scale)
        line.pieces.append(_Piece(text, loaded, x, width))
        x += width
    line.width = x
    line.ink_right = max(line.ink_right, x)
    return line


def _paste_scaled(image: Image.Image, piece: _Piece, x: float, baseline: float) -> None:
    """Draw a bitmap-strike piece at its strike size, then scale it into place."""
    font, scale = piece.font.font, piece.font.scale
    left, top, right, bottom = font.getbbox(piece.text, anchor="ls")
    if right <= left or bottom <= top:
        return
    tile = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((-left, -top), piece.text, font=font, anchor="ls", embedded_color=True)
    size = (max(1, round(tile.width * scale)), max(1, round(tile.height * scale)))
    tile = tile.resize(size, Image.Resampling.LANCZOS)
    image.alpha_composite(tile, (max(0, round(x + left * scale)), max(0, round(baseline + top * scale))))
