"""Windows font backend: the fonts and fallback configuration of the Windows install itself.

- Installed faces: the font files the registry lists (HKLM and HKCU ...\\Fonts), read
  with fontTools: family, style, PostScript name, weight class, italic bit, colour
  tables and character map. Reading a few hundred fonts takes seconds, so the result
  is kept in <home>/fonts-windows.json per file + modification time; only new or
  changed files are read again. warm_up() starts that work in the background when the
  app starts, so the first caption does not wait for it.
- Base face: the face the user picked, an uploaded file, or the Windows UI font - the
  "message" font from SystemParametersInfo(SPI_GETNONCLIENTMETRICS) - at the weight
  class nearest Regular or Bold (OpenType 400 / 700), upright before italic.
- After the base face: Windows' font-link list for its family (registry
  ...\\FontLink\\SystemLink, the list GDI and Uniscribe fall back through, in that
  order), then every other face - nearest in weight first and, between equals, the
  one with more characters (general-purpose fonts before single-script display faces).
- Each grapheme cluster goes to the first face in that order that has it, by the
  shared rule in chain.py (emoji to colour faces, whitespace kept with the face before).

Only _registered_files, _links and _ui_family touch Windows; the rest is plain code
(tests run it on other systems with those three replaced).

Not in here: loading or drawing (faces.py, render.py).
Called by: backend.py.
"""
from __future__ import annotations

import bisect
import ctypes
import functools
import json
import logging
import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont, TTLibError

from .. import paths
from . import chain
from .faces import Base, Face, FaceInfo, covers

_FONTS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
_LINK_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\FontLink\SystemLink"
_OPENTYPE_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")  # what FreeType and fontTools read as OpenType
_COLLECTION_TAG = b"ttcf"
_REGULAR, _BOLD = 400, 700                             # OpenType usWeightClass of Regular and Bold
_ITALIC_BIT = 1                                        # OS/2 fsSelection bit 0
_COLOR_TABLES = ("sbix", "CBDT", "COLR")
_SPI_GETNONCLIENTMETRICS = 0x0029                      # winuser.h
_LF_FACESIZE = 32                                      # wingdi.h
_CACHE_NAME = "fonts-windows.json"


def available() -> bool:
    return sys.platform == "win32"


@dataclass(frozen=True)
class _Entry:
    face: Face
    family: str
    style: str
    weight: int
    italic: bool
    color: bool
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    count: int

    def has(self, codepoint: int) -> bool:
        i = bisect.bisect_right(self.starts, codepoint) - 1
        return i >= 0 and codepoint <= self.ends[i]


# ---- The three Windows touch points -------------------------------------------------------

def _values(hive, key_path: str) -> list[tuple[str, object]]:
    import winreg

    try:
        key = winreg.OpenKey(hive, key_path)
    except OSError:
        return []
    out = []
    with key:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            out.append((name, value))
            index += 1
    return out


def _registered_files() -> list[Path]:
    import winreg

    fonts_dir = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot", "")) / "Fonts"
    found: dict[str, Path] = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for _, value in _values(hive, _FONTS_KEY):
            path = Path(str(value))
            path = path if path.is_absolute() else fonts_dir / path
            if path.suffix.lower() in _OPENTYPE_SUFFIXES and path.is_file():
                found.setdefault(str(path).casefold(), path)
    return list(found.values())


def _links(family: str) -> list[tuple[str, str]]:
    """Windows' font-link list for `family`: [(file name, face name)], in order."""
    import winreg

    for name, value in _values(winreg.HKEY_LOCAL_MACHINE, _LINK_KEY):
        if name.casefold() == family.casefold():
            out = []
            for item in value if isinstance(value, list) else [value]:
                parts = [p.strip() for p in str(item).split(",")]
                out.append((parts[0], parts[1] if len(parts) > 1 else ""))
            return out
    return []


def _ui_family() -> str:
    class LOGFONTW(ctypes.Structure):
        _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long), ("lfEscapement", ctypes.c_long),
                    ("lfOrientation", ctypes.c_long), ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
                    ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte), ("lfCharSet", ctypes.c_byte),
                    ("lfOutPrecision", ctypes.c_byte), ("lfClipPrecision", ctypes.c_byte),
                    ("lfQuality", ctypes.c_byte), ("lfPitchAndFamily", ctypes.c_byte),
                    ("lfFaceName", ctypes.c_wchar * _LF_FACESIZE)]

    class NONCLIENTMETRICSW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("iBorderWidth", ctypes.c_int), ("iScrollWidth", ctypes.c_int),
                    ("iScrollHeight", ctypes.c_int), ("iCaptionWidth", ctypes.c_int),
                    ("iCaptionHeight", ctypes.c_int), ("lfCaptionFont", LOGFONTW),
                    ("iSmCaptionWidth", ctypes.c_int), ("iSmCaptionHeight", ctypes.c_int),
                    ("lfSmCaptionFont", LOGFONTW), ("iMenuWidth", ctypes.c_int), ("iMenuHeight", ctypes.c_int),
                    ("lfMenuFont", LOGFONTW), ("lfStatusFont", LOGFONTW), ("lfMessageFont", LOGFONTW),
                    ("iPaddedBorderWidth", ctypes.c_int)]

    metrics = NONCLIENTMETRICSW()
    metrics.cbSize = ctypes.sizeof(metrics)
    ok = ctypes.windll.user32.SystemParametersInfoW(_SPI_GETNONCLIENTMETRICS, metrics.cbSize,
                                                    ctypes.byref(metrics), 0)
    return metrics.lfMessageFont.lfFaceName if ok else ""


# ---- Index of installed faces ----------------------------------------------------------------

def _ranges(codepoints: list[int]) -> tuple[list[int], list[int]]:
    starts: list[int] = []
    ends: list[int] = []
    for cp in codepoints:
        if ends and cp == ends[-1] + 1:
            ends[-1] = cp
        else:
            starts.append(cp)
            ends.append(cp)
    return starts, ends


def _read(path: Path) -> list[dict]:
    logging.getLogger("fontTools").setLevel(logging.ERROR)  # quiet about harmless quirks in system fonts
    with open(path, "rb") as fh:
        collection = fh.read(len(_COLLECTION_TAG)) == _COLLECTION_TAG
    fonts = TTCollection(str(path), lazy=True).fonts if collection else [TTFont(str(path), lazy=True)]
    records = []
    try:
        for number, font in enumerate(fonts):
            names = font["name"]
            os2 = font["OS/2"] if "OS/2" in font else None
            codepoints = sorted(font.getBestCmap() or {})
            starts, ends = _ranges(codepoints)
            records.append({
                "index": number,
                "ps": names.getDebugName(6) or "",
                "family": names.getDebugName(16) or names.getDebugName(1) or path.stem,
                "style": names.getDebugName(17) or names.getDebugName(2) or "",
                "weight": os2.usWeightClass if os2 else _REGULAR,
                "italic": bool(os2 and os2.fsSelection & _ITALIC_BIT),
                "color": any(tag in font for tag in _COLOR_TABLES),
                "starts": starts, "ends": ends, "count": len(codepoints),
            })
    finally:
        for font in fonts:
            font.close()
    return records


def build_index(files: list[Path], cache_path: Path) -> tuple[_Entry, ...]:
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    fresh: dict[str, dict] = {}
    for path in files:
        key, mtime = str(path), path.stat().st_mtime_ns
        known = cache.get(key)
        if known and known.get("mtime") == mtime:
            fresh[key] = known
            continue
        try:
            fresh[key] = {"mtime": mtime, "faces": _read(path)}
        except (TTLibError, OSError, KeyError, ValueError, AssertionError):
            fresh[key] = {"mtime": mtime, "faces": []}  # unreadable: remembered, not retried every start
    if fresh != cache:
        fd, tmp = tempfile.mkstemp(dir=cache_path.parent, prefix=".fonts-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(fresh, fh)
        os.replace(tmp, cache_path)
    return tuple(
        _Entry(Face(key, f["index"], f["ps"]), f["family"], f["style"], f["weight"], f["italic"], f["color"],
               tuple(f["starts"]), tuple(f["ends"]), f["count"])
        for key, record in fresh.items() for f in record["faces"]
    )


_INDEX_LOCK = threading.Lock()


@functools.cache  # one index per process: fonts installed while the app runs appear after a restart
def _built() -> tuple[_Entry, ...]:
    return build_index(_registered_files(), paths.home() / _CACHE_NAME)


def _entries() -> tuple[_Entry, ...]:
    with _INDEX_LOCK:  # the warm-up thread and a first caption must not both read every font
        return _built()


def warm_up() -> None:
    """Start reading the installed fonts in the background (once per process)."""
    if available() and not _WARMING.is_set():
        _WARMING.set()
        threading.Thread(target=_entries, name="font-index", daemon=True).start()


_WARMING = threading.Event()


# ---- Order ----------------------------------------------------------------------------------

def _nearest(candidates: list[_Entry], weight: int) -> _Entry | None:
    return min(candidates, key=lambda e: (e.italic, abs(e.weight - weight)), default=None)


def _base_entry(base: Base) -> _Entry | None:
    entries = _entries()
    if base.kind == "ps":
        picked = next((e for e in entries if e.face.ps_name == base.value), None)
        if picked:
            return picked
    family = _ui_family().casefold()
    return _nearest([e for e in entries if e.family.casefold() == family], _BOLD if base.bold else _REGULAR)


def _linked(base_entry: _Entry) -> list[_Entry]:
    entries = _entries()
    out: list[_Entry] = []
    for file_name, face_name in _links(base_entry.family):
        in_file = [e for e in entries if Path(e.face.path).name.casefold() == file_name.casefold()]
        named = [e for e in in_file if face_name and e.family.casefold() == face_name.casefold()] or in_file
        best = _nearest(named, base_entry.weight)
        if best and best not in out:
            out.append(best)
    return out


@functools.cache  # one order per font choice in use
def _order(kind: str, value: str, bold: bool) -> tuple[_Entry, ...]:
    base_entry = _base_entry(Base(kind, value, bold))
    if base_entry is None:
        return tuple(sorted(_entries(), key=lambda e: -e.count))
    linked = _linked(base_entry)
    first = [base_entry] + [e for e in linked if e != base_entry]
    rest = sorted((e for e in _entries() if e not in first),
                  key=lambda e: (abs(e.weight - base_entry.weight), e.italic, -e.count))
    return tuple(first + rest)


def face_for(base: Base, size: float) -> Face:
    if base.kind == "file":
        return Face(base.value, 0)
    order = _order(base.kind, base.value, base.bold)
    return order[0].face if order else Face("")


def segment(text: str, base: Base, size: float) -> list[tuple[int, int, Face]]:
    head = Face(base.value, 0) if base.kind == "file" else None
    order = _order("ui", "", base.bold) if head else _order(base.kind, base.value, base.bold)

    def candidates(ch: str):
        if head and covers(head, ch):
            yield head
        cp = ord(ch)
        yield from (e.face for e in order if e.has(cp))

    return chain.segment(text, candidates, head or (order[0].face if order else Face("")))


def list_faces() -> list[FaceInfo]:
    seen = {e.face.ps_name: FaceInfo(f"ps:{e.face.ps_name}", e.family, e.style)
            for e in _entries() if e.face.ps_name}
    return sorted(seen.values(), key=lambda f: (f.family.casefold(), f.style.casefold()))
