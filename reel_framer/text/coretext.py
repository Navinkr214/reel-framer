"""macOS font backend: CoreText (the OS text engine) decides which installed
face draws each part of a caption, the same fallback native macOS apps use.

- segment(text, base, size): lays the text out as one CTLine in the base face.
  CoreText splits it into glyph runs and switches to its cascade-list faces
  where the base face has no glyphs (Devanagari, emoji, CJK, ...), matching
  the base face's weight. Each run comes back as (start, end, Face), with
  Python string indices (CoreText counts UTF-16 units; they are converted).
- list_faces(): every installed face a user can pick (Apple's hidden system
  faces, whose PostScript names start with ".", are left out).
- Base faces: the OS UI font (regular or emphasised), a face picked by
  PostScript name, or an uploaded font file.

Everything CoreText creates here is released before returning; the app is a
long-running server, so a leak per caption would grow without bound.

Not in here: loading faces into Pillow (faces.py) or drawing (render.py).
Called by: backend.py.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
import sys
from .faces import Base, Face, FaceInfo

# Constants from Apple's SDK headers (CFString.h, CFNumber.h, CTFont.h).
_UTF8 = 0x08000100                 # kCFStringEncodingUTF8
_NUMBER_SINT64 = 4                 # kCFNumberSInt64Type
_NUMBER_FLOAT64 = 6                # kCFNumberFloat64Type
_UI_FONT_SYSTEM = 2                # kCTFontUIFontSystem
_UI_FONT_EMPHASIZED_SYSTEM = 3     # kCTFontUIFontEmphasizedSystem
_HIDDEN_PREFIX = "."               # Apple marks private system faces with a leading dot


class _CFRange(ctypes.Structure):
    _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]


class _Lib:
    """The CoreFoundation / CoreText functions this module calls."""

    def __init__(self) -> None:
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        ct = ctypes.CDLL(ctypes.util.find_library("CoreText"))
        vp, lng, dbl, boo, u32 = ctypes.c_void_p, ctypes.c_long, ctypes.c_double, ctypes.c_bool, ctypes.c_uint32

        def fn(lib, name, res, *args):
            f = getattr(lib, name)
            f.restype, f.argtypes = res, list(args)
            return f

        self.release = fn(cf, "CFRelease", None, vp)
        self.str_create = fn(cf, "CFStringCreateWithBytes", vp, vp, ctypes.c_char_p, lng, u32, boo)
        self.str_length = fn(cf, "CFStringGetLength", lng, vp)
        self.str_max_size = fn(cf, "CFStringGetMaximumSizeForEncoding", lng, lng, u32)
        self.str_get = fn(cf, "CFStringGetCString", boo, vp, ctypes.c_char_p, lng, u32)
        self.dict_create = fn(cf, "CFDictionaryCreate", vp, vp, ctypes.POINTER(vp), ctypes.POINTER(vp), lng, vp, vp)
        self.dict_get = fn(cf, "CFDictionaryGetValue", vp, vp, vp)
        self.dict_count = fn(cf, "CFDictionaryGetCount", lng, vp)
        self.dict_items = fn(cf, "CFDictionaryGetKeysAndValues", None, vp, ctypes.POINTER(vp), ctypes.POINTER(vp))
        self.number_get = fn(cf, "CFNumberGetValue", boo, vp, lng, vp)
        self.array_count = fn(cf, "CFArrayGetCount", lng, vp)
        self.array_get = fn(cf, "CFArrayGetValueAtIndex", vp, vp, lng)
        self.attr_str_create = fn(cf, "CFAttributedStringCreate", vp, vp, vp, vp)
        self.url_from_path = fn(cf, "CFURLCreateFromFileSystemRepresentation", vp, vp, ctypes.c_char_p, lng, boo)
        self.url_to_path = fn(cf, "CFURLGetFileSystemRepresentation", boo, vp, boo, ctypes.c_char_p, lng)
        self.ui_font = fn(ct, "CTFontCreateUIFontForLanguage", vp, u32, dbl, vp)
        self.font_named = fn(ct, "CTFontCreateWithName", vp, vp, dbl, vp)
        self.font_from_desc = fn(ct, "CTFontCreateWithFontDescriptor", vp, vp, dbl, vp)
        self.descs_from_url = fn(ct, "CTFontManagerCreateFontDescriptorsFromURL", vp, vp)
        self.font_attr = fn(ct, "CTFontCopyAttribute", vp, vp, vp)
        self.font_ps_name = fn(ct, "CTFontCopyPostScriptName", vp, vp)
        self.font_family = fn(ct, "CTFontCopyFamilyName", vp, vp)
        self.font_name = fn(ct, "CTFontCopyName", vp, vp, vp)
        self.font_variation = fn(ct, "CTFontCopyVariation", vp, vp)
        self.line_create = fn(ct, "CTLineCreateWithAttributedString", vp, vp)
        self.line_runs = fn(ct, "CTLineGetGlyphRuns", vp, vp)
        self.run_attrs = fn(ct, "CTRunGetAttributes", vp, vp)
        self.run_range = fn(ct, "CTRunGetStringRange", _CFRange, vp)
        self.all_ps_names = fn(ct, "CTFontManagerCopyAvailablePostScriptNames", vp)
        self.FONT_ATTR = vp.in_dll(ct, "kCTFontAttributeName").value
        self.URL_ATTR = vp.in_dll(ct, "kCTFontURLAttribute").value
        self.STYLE_NAME = vp.in_dll(ct, "kCTFontStyleNameKey").value
        self.KEY_CALLBACKS = ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        self.VALUE_CALLBACKS = ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        self.path_max = os.pathconf("/", "PC_PATH_MAX")


_lib: _Lib | None = None
_load_error: str | None = None


def available() -> bool:
    global _lib, _load_error
    if _lib is None and _load_error is None:
        if sys.platform != "darwin":
            _load_error = "not macOS"
        else:
            try:
                _lib = _Lib()
            except (OSError, AttributeError, ValueError) as exc:
                _load_error = str(exc)
    return _lib is not None


class _Owned:
    """Collects CoreFoundation objects this module created and releases them on exit."""

    def __init__(self) -> None:
        self.refs: list[int] = []

    def __call__(self, ref: int | None) -> int | None:
        if ref:
            self.refs.append(ref)
        return ref

    def __enter__(self) -> "_Owned":
        return self

    def __exit__(self, *exc) -> None:
        for ref in reversed(self.refs):
            _lib.release(ref)


def _cfstr(own: _Owned, text: str) -> int:
    raw = text.encode("utf-8")
    return own(_lib.str_create(None, raw, len(raw), _UTF8, False))


def _pystr(ref: int | None) -> str:
    if not ref:
        return ""
    size = _lib.str_max_size(_lib.str_length(ref), _UTF8) + 1
    buf = ctypes.create_string_buffer(size)
    return buf.value.decode("utf-8") if _lib.str_get(ref, buf, size, _UTF8) else ""


def _copied_str(own: _Owned, ref: int | None) -> str:
    return _pystr(own(ref))


def _base_font(own: _Owned, base: Base, size: float) -> int:
    if base.kind == "ps":
        return own(_lib.font_named(_cfstr(own, base.value), size, None))
    if base.kind == "file":
        raw = os.fsencode(base.value)
        url = own(_lib.url_from_path(None, raw, len(raw), False))
        descs = own(_lib.descs_from_url(url)) if url else None
        if descs and _lib.array_count(descs) > 0:
            return own(_lib.font_from_desc(_lib.array_get(descs, 0), size, None))
        raise ValueError(f"CoreText cannot read the font file {os.path.basename(base.value)}")
    kind = _UI_FONT_EMPHASIZED_SYSTEM if base.bold else _UI_FONT_SYSTEM
    return own(_lib.ui_font(kind, size, None))


def _face_of(own: _Owned, font: int) -> Face:
    url = own(_lib.font_attr(font, _lib.URL_ATTR))
    buf = ctypes.create_string_buffer(_lib.path_max)
    path = os.fsdecode(buf.value) if url and _lib.url_to_path(url, True, buf, _lib.path_max) else ""
    return Face(
        path=path,
        index=None,
        ps_name=_copied_str(own, _lib.font_ps_name(font)),
        axes=_variation(own, font),
    )


def _variation(own: _Owned, font: int) -> tuple[tuple[str, float], ...]:
    var = own(_lib.font_variation(font))
    if not var:
        return ()
    count = _lib.dict_count(var)
    keys, values = (ctypes.c_void_p * count)(), (ctypes.c_void_p * count)()
    _lib.dict_items(var, keys, values)
    axes = []
    for key, value in zip(keys, values):
        tag, coord = ctypes.c_int64(), ctypes.c_double()
        _lib.number_get(key, _NUMBER_SINT64, ctypes.byref(tag))
        _lib.number_get(value, _NUMBER_FLOAT64, ctypes.byref(coord))
        # CoreText keys axes by their four-character tag packed into an integer.
        axes.append((struct.pack(">I", tag.value & 0xFFFFFFFF).decode("latin-1"), coord.value))
    return tuple(sorted(axes))


def _utf16_to_index(text: str) -> list[int]:
    """Map every UTF-16 offset of `text` (plus its end) to a Python string index."""
    table: list[int] = []
    for i, ch in enumerate(text):
        table.extend([i] * (2 if ord(ch) > 0xFFFF else 1))  # astral characters take two units
    table.append(len(text))
    return table


def segment(text: str, base: Base, size: float) -> list[tuple[int, int, Face]]:
    if not text:
        return []
    with _Owned() as own:
        font = _base_font(own, base, size)
        keys = (ctypes.c_void_p * 1)(_lib.FONT_ATTR)
        values = (ctypes.c_void_p * 1)(font)
        attrs = own(_lib.dict_create(None, keys, values, 1, _lib.KEY_CALLBACKS, _lib.VALUE_CALLBACKS))
        line = own(_lib.line_create(own(_lib.attr_str_create(None, _cfstr(own, text), attrs))))
        runs = _lib.line_runs(line)  # owned by the line
        index_of = _utf16_to_index(text)
        faces: dict[tuple, Face] = {}
        spans = []
        for i in range(_lib.array_count(runs)):
            run = _lib.array_get(runs, i)
            rng = _lib.run_range(run)
            run_font = _lib.dict_get(_lib.run_attrs(run), _lib.FONT_ATTR) or font
            face = _face_of(own, run_font)
            face = faces.setdefault((face.path, face.ps_name, face.axes), face)
            start = index_of[min(rng.location, len(index_of) - 1)]
            end = index_of[min(rng.location + rng.length, len(index_of) - 1)]
            spans.append((start, end, face))
        return _cover(sorted(spans, key=lambda s: s[0]), len(text), _face_of(own, font))


def _cover(spans, length: int, fallback: Face) -> list[tuple[int, int, Face]]:
    """Make the spans tile [0, length) and merge neighbours that share a face."""
    out: list[tuple[int, int, Face]] = []
    pos = 0
    for start, end, face in spans:
        if end <= pos:
            continue
        start = max(start, pos)
        if start > pos:  # a gap CoreText left: give it to the previous face
            out.append((pos, start, out[-1][2] if out else face))
        out.append((start, end, face))
        pos = end
    if pos < length:
        out.append((pos, length, out[-1][2] if out else fallback))
    merged: list[tuple[int, int, Face]] = []
    for start, end, face in out:
        if merged and merged[-1][2] == face and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end, face)
        else:
            merged.append((start, end, face))
    return merged


def face_for(base: Base, size: float) -> Face:
    """The Face CoreText uses for `base` itself (for metrics of blank lines)."""
    with _Owned() as own:
        return _face_of(own, _base_font(own, base, size))


def list_faces() -> list[FaceInfo]:
    out: list[FaceInfo] = []
    with _Owned() as own:
        names = own(_lib.all_ps_names())
        for i in range(_lib.array_count(names) if names else 0):
            ps = _pystr(_lib.array_get(names, i))
            if not ps or ps.startswith(_HIDDEN_PREFIX):
                continue
            with _Owned() as inner:
                font = inner(_lib.font_named(_cfstr(inner, ps), 0.0, None))
                if not font:
                    continue
                family = _copied_str(inner, _lib.font_family(font))
                style = _copied_str(inner, _lib.font_name(font, _lib.STYLE_NAME))
            out.append(FaceInfo(id=f"ps:{ps}", family=family or ps, style=style))
    return sorted(out, key=lambda f: (f.family.casefold(), f.style.casefold()))
