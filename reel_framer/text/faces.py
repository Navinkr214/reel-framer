"""Font faces, and loading one into Pillow at a pixel size.

A Face names one drawable face on disk: the file, the face inside a font
collection (.ttc), and variation-axis coordinates for variable fonts. The
backends (coretext.py, fontconfig.py) produce Faces; render.py loads them here.

Loading handles what a plain `ImageFont.truetype(path, size)` gets wrong:
- collections: when a backend reports only the PostScript name (CoreText gives
  file + name, not the face number), the face is found by that name;
- variable fonts: the backend's axis coordinates are applied in the font's own
  fvar axis order, which is the order Pillow expects;
- bitmap-only colour fonts (sbix / CBDT emoji): FreeType opens them only at
  one of the font's own strike sizes, so the smallest strike at or above the
  wanted size is opened (the largest when none is that big) and the caller
  scales the drawn pixels by LoadedFont.scale.

Not in here: choosing which face draws which characters (the backends) or
drawing (render.py).
Called by: render.py, backend.py.
"""
from __future__ import annotations

import functools
import math
import os
from dataclasses import dataclass

from fontTools.ttLib import TTCollection, TTFont
from PIL import ImageFont, features

# FreeType face_index layout: the low 16 bits are the face number inside a
# collection; bits 16+ select a named instance (fontconfig reports it that way).
_FACE_NUMBER_MASK = 0xFFFF
# OpenType collection files start with this tag.
_COLLECTION_TAG = b"ttcf"
# Tables that carry colour glyphs: sbix (Apple bitmaps), CBDT (Google bitmaps), COLR (vector layers).
_COLOR_TABLES = ("sbix", "CBDT", "COLR")


@dataclass(frozen=True)
class Face:
    path: str                    # font file; "" = Pillow's bundled font
    index: int | None = None     # FreeType face_index; None = find the face by ps_name
    ps_name: str = ""
    axes: tuple[tuple[str, float], ...] = ()  # variation coordinates by axis tag


@dataclass(frozen=True)
class Base:
    """The face a caption starts from (Settings TextStyle.font + bold)."""
    kind: str          # "ui" (the OS UI font) | "ps" (PostScript name) | "file" (uploaded font)
    value: str = ""    # PostScript name or file path
    bold: bool = True  # weight of the OS UI font (kind "ui")


@dataclass(frozen=True)
class FaceInfo:
    """One installed face, for the font picker."""
    id: str            # "ps:<PostScript name>" (the Settings value)
    family: str
    style: str


@dataclass(frozen=True)
class LoadedFont:
    font: ImageFont.FreeTypeFont
    scale: float   # multiply drawn pixels by this (bitmap strikes); 1.0 for scalable faces
    color: bool    # the face has colour glyphs: draw with embedded_color


@dataclass(frozen=True)
class _Tables:
    axes: tuple[tuple[str, float], ...]  # fvar axes in font order: (tag, default value)
    strikes: tuple[int, ...]             # strike sizes of a bitmap-only colour face
    color: bool


def layout_engine() -> ImageFont.Layout:
    """RAQM (HarfBuzz shaping + FriBiDi) when Pillow has it; complex scripts need it."""
    return ImageFont.Layout.RAQM if features.check_feature("raqm") else ImageFont.Layout.BASIC


def load(face: Face, px: float) -> LoadedFont:
    if not face.path:
        return LoadedFont(ImageFont.load_default(size=px), 1.0, False)
    mtime = os.stat(face.path).st_mtime_ns
    index = face.index if face.index is not None else _collection_index(face.path, mtime, face.ps_name)
    tables = _tables(face.path, mtime, index & _FACE_NUMBER_MASK)
    size, scale = px, 1.0
    if tables.strikes:
        wanted = math.ceil(px)
        size = min((s for s in tables.strikes if s >= wanted), default=max(tables.strikes))
        scale = px / size
    font = ImageFont.truetype(face.path, size=size, index=index, layout_engine=layout_engine())
    if face.axes and tables.axes:
        coords = dict(face.axes)
        font.set_variation_by_axes([coords.get(tag, default) for tag, default in tables.axes])
    return LoadedFont(font, scale, tables.color)


def has_color_glyphs(face: Face) -> bool:
    """True when the face draws colour glyphs (sbix / CBDT / COLR emoji fonts)."""
    if not face.path:
        return False
    mtime = os.stat(face.path).st_mtime_ns
    index = face.index if face.index is not None else _collection_index(face.path, mtime, face.ps_name)
    return _tables(face.path, mtime, index & _FACE_NUMBER_MASK).color


def covers(face: Face, text: str) -> bool:
    """True when the face's character map has every character of `text`."""
    if not face.path:
        return True
    mtime = os.stat(face.path).st_mtime_ns
    index = face.index if face.index is not None else _collection_index(face.path, mtime, face.ps_name)
    cmap = _cmap(face.path, mtime, index & _FACE_NUMBER_MASK)
    return all(ord(ch) in cmap for ch in text)


# The caches below are keyed by file + modification time, so they hold at most one
# entry per installed face in use, and a replaced font file is read again.

@functools.cache
def _collection_index(path: str, mtime_ns: int, ps_name: str) -> int:
    with open(path, "rb") as fh:
        if fh.read(len(_COLLECTION_TAG)) != _COLLECTION_TAG or not ps_name:
            return 0
    collection = TTCollection(path, lazy=True)
    try:
        for number, font in enumerate(collection.fonts):
            if font["name"].getDebugName(6) == ps_name:  # name ID 6 = PostScript name
                return number
    finally:
        collection.close()
    return 0


@functools.cache
def _tables(path: str, mtime_ns: int, face_number: int) -> _Tables:
    font = TTFont(path, fontNumber=face_number, lazy=True)
    try:
        axes = tuple((a.axisTag, a.defaultValue) for a in font["fvar"].axes) if "fvar" in font else ()
        strikes: tuple[int, ...] = ()
        if "sbix" in font:
            strikes = tuple(sorted(font["sbix"].strikes))
        elif "CBLC" in font:
            strikes = tuple(sorted({s.bitmapSizeTable.ppemY for s in font["CBLC"].strikes}))
        return _Tables(axes, strikes, any(tag in font for tag in _COLOR_TABLES))
    finally:
        font.close()


@functools.cache
def _cmap(path: str, mtime_ns: int, face_number: int) -> frozenset[int]:
    font = TTFont(path, fontNumber=face_number, lazy=True)
    try:
        return frozenset(font.getBestCmap() or {})
    finally:
        font.close()
