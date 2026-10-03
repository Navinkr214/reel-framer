"""Font backend for machines with fontconfig (Linux servers; macOS uses CoreText).

- segment(text, base, size): fontconfig's own sorted fallback chain for the base
  face (`fc-match -s`: faces in the configured preference order, keeping only
  those that add coverage). Each grapheme cluster goes to the first face in
  the chain that has it, by the shared rule in chain.py (emoji to colour faces,
  whitespace kept with the face before it).
- list_faces(): the installed faces, via `fc-list`.

Not in here: loading faces or drawing (faces.py, render.py).
Called by: backend.py.
"""
from __future__ import annotations

import bisect
import functools
import shutil
import subprocess

from . import chain
from .faces import Base, Face, FaceInfo, covers

_CHAIN_FORMAT = "%{file}\t%{index}\t%{postscriptname}\t%{charset}\n"
_LIST_FORMAT = "%{postscriptname}\t%{family[0]}\t%{style[0]}\n"


def available() -> bool:
    return shutil.which("fc-match") is not None and shutil.which("fc-list") is not None


def _escape(value: str) -> str:
    """Escape fontconfig pattern syntax characters in a property value."""
    return "".join("\\" + ch if ch in "\\-:,=" else ch for ch in value)


def _pattern(base: Base) -> str:
    if base.kind == "ps":
        return f":postscriptname={_escape(base.value)}"
    return "sans-serif:weight=bold" if base.bold else "sans-serif"


@functools.cache  # one chain per font choice in use
def _chain(pattern: str) -> tuple[tuple[Face, tuple[int, ...], tuple[int, ...]], ...]:
    proc = subprocess.run(["fc-match", "-s", "--format", _CHAIN_FORMAT, pattern], capture_output=True, text=True)
    chain = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or not parts[0]:
            continue
        starts, ends = [], []
        for item in parts[3].split():
            lo, _, hi = item.partition("-")
            starts.append(int(lo, 16))
            ends.append(int(hi or lo, 16))
        chain.append((Face(parts[0], int(parts[1] or 0), parts[2]), tuple(starts), tuple(ends)))
    return tuple(chain)


def _has(starts: tuple[int, ...], ends: tuple[int, ...], codepoint: int) -> bool:
    i = bisect.bisect_right(starts, codepoint) - 1
    return i >= 0 and codepoint <= ends[i]


def _head(base: Base) -> Face | None:
    return Face(base.value, 0) if base.kind == "file" else None


def face_for(base: Base, size: float) -> Face:
    head = _head(base)
    if head:
        return head
    chain = _chain(_pattern(base))
    return chain[0][0] if chain else Face("")


def segment(text: str, base: Base, size: float) -> list[tuple[int, int, Face]]:
    head = _head(base)
    links = _chain(_pattern(Base("ui", bold=base.bold)) if head else _pattern(base))

    def candidates(ch: str):
        if head and covers(head, ch):
            yield head
        cp = ord(ch)
        yield from (face for face, starts, ends in links if _has(starts, ends, cp))

    return chain.segment(text, candidates, head or (links[0][0] if links else Face("")))


def list_faces() -> list[FaceInfo]:
    proc = subprocess.run(["fc-list", "--format", _LIST_FORMAT], capture_output=True, text=True)
    seen: dict[str, FaceInfo] = {}
    for line in proc.stdout.splitlines():
        ps, _, rest = line.partition("\t")
        family, _, style = rest.partition("\t")
        if ps and ps not in seen:
            seen[ps] = FaceInfo(id=f"ps:{ps}", family=family or ps, style=style)
    return sorted(seen.values(), key=lambda f: (f.family.casefold(), f.style.casefold()))
