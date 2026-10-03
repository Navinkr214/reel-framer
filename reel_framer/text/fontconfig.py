"""Font backend for machines with fontconfig (Linux servers; macOS uses CoreText).

- segment(text, base, size): fontconfig's own sorted fallback chain for the base
  face (`fc-match -s`: faces in the configured preference order, keeping only
  those that add coverage). Each grapheme cluster goes to the first face in
  the chain whose charset has the cluster's first character; the cluster's
  marks belong to that character's script, so that face draws them too.
  A cluster Unicode presents as emoji (Emoji_Presentation, or followed by
  VARIATION SELECTOR-16) goes to the first such face that has colour glyphs,
  when there is one: plain fonts often carry black outline versions of ❤ ☺ ✈.
  Whitespace stays with the face before it, so a run is not split at spaces.
- list_faces(): the installed faces, via `fc-list`.

Not in here: loading faces or drawing (faces.py, render.py).
Called by: backend.py.
"""
from __future__ import annotations

import bisect
import functools
import shutil
import subprocess

import regex

from .faces import Base, Face, FaceInfo, covers, has_color_glyphs

_CHAIN_FORMAT = "%{file}\t%{index}\t%{postscriptname}\t%{charset}\n"
_LIST_FORMAT = "%{postscriptname}\t%{family[0]}\t%{style[0]}\n"
# Unicode's own emoji presentation: drawn as emoji by default, or followed by U+FE0F.
_EMOJI = regex.compile(r"\p{Emoji_Presentation}|\uFE0F")


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
    chain = _chain(_pattern(Base("ui", bold=base.bold)) if head else _pattern(base))
    first = head or (chain[0][0] if chain else Face(""))
    spans: list[tuple[int, int, Face]] = []
    for match in regex.finditer(r"\X", text):
        cluster = match.group()
        if cluster.isspace() and spans:
            face = spans[-1][2]
        else:
            cp = ord(cluster[0])
            covering = ([head] if head and covers(head, cluster[0]) else []) + [
                f for f, starts, ends in chain if _has(starts, ends, cp)]
            if covering and _EMOJI.search(cluster):
                face = next((f for f in covering if has_color_glyphs(f)), covering[0])
            else:
                face = covering[0] if covering else first
        if spans and spans[-1][2] == face:
            spans[-1] = (spans[-1][0], match.end(), face)
        else:
            spans.append((match.start(), match.end(), face))
    return spans


def list_faces() -> list[FaceInfo]:
    proc = subprocess.run(["fc-list", "--format", _LIST_FORMAT], capture_output=True, text=True)
    seen: dict[str, FaceInfo] = {}
    for line in proc.stdout.splitlines():
        ps, _, rest = line.partition("\t")
        family, _, style = rest.partition("\t")
        if ps and ps not in seen:
            seen[ps] = FaceInfo(id=f"ps:{ps}", family=family or ps, style=style)
    return sorted(seen.values(), key=lambda f: (f.family.casefold(), f.style.casefold()))
