"""Picks the font backend for this machine: the one interface render.py and the
Settings tab use.

Order: CoreText on macOS (the OS's own per-script fallback), the Windows font
registry and font links on Windows (windows.py), else fontconfig when `fc-match`
is on PATH, else "none". With "none" a caption is drawn with the chosen font only
(an uploaded file, or Pillow's bundled font) and gets no per-script fallback; the
Settings tab says so. $REEL_FRAMER_FONT_BACKEND ("coretext" / "windows" /
"fontconfig" / "none") forces one, for tests.

Font setting values (Settings TextStyle.font):
  ""              the OS UI font, bold or regular per TextStyle.bold
  "ps:<name>"     an installed face, by PostScript name
  "asset:<file>"  a font file uploaded in Settings

Not in here: the backends themselves (coretext.py, fontconfig.py).
Called by: render.py, ui/settings_tab.py.
"""
from __future__ import annotations

import os

from .. import assets
from . import coretext, fontconfig, windows
from .faces import Base, Face, FaceInfo

_BACKENDS = {"coretext": coretext, "windows": windows, "fontconfig": fontconfig}


def _backend():
    forced = os.environ.get("REEL_FRAMER_FONT_BACKEND", "").lower()
    if forced:
        module = _BACKENDS.get(forced)
        return module if module and module.available() else None
    return next((m for m in _BACKENDS.values() if m.available()), None)


def name() -> str:
    backend = _backend()
    return {coretext: "CoreText", windows: "Windows fonts", fontconfig: "fontconfig"}.get(backend, "none")


def base_from_setting(font: str, bold: bool) -> Base:
    if font.startswith("ps:"):
        return Base("ps", font[len("ps:"):], bold)
    if font.startswith("asset:"):
        return Base("file", str(assets.path_of(font[len("asset:"):])), bold)
    return Base("ui", "", bold)


def segment(text: str, base: Base, size: float) -> list[tuple[int, int, Face]]:
    """Split `text` into runs, each with the face that draws it: [(start, end, face)]."""
    backend = _backend()
    if backend:
        return backend.segment(text, base, size)
    return [(0, len(text), face_for(base, size))] if text else []


def face_for(base: Base, size: float) -> Face:
    backend = _backend()
    if backend:
        return backend.face_for(base, size)
    return Face(base.value if base.kind == "file" else "")


def warm_up() -> None:
    """Let the backend prepare in the background (the Windows one reads every installed font once)."""
    backend = _backend()
    if backend is windows:
        windows.warm_up()


def list_faces() -> list[FaceInfo]:
    backend = _backend()
    return backend.list_faces() if backend else []
