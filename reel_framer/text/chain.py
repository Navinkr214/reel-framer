"""Give each grapheme cluster of a text the first face, in a preference order, that
can draw it. Shared by the backends that build their own fallback chain
(fontconfig.py on Linux, windows.py); on macOS CoreText does this itself.

- The cluster's first character decides: the cluster's marks belong to that
  character's script, so the face that has it draws them too.
- A cluster Unicode presents as emoji (Emoji_Presentation, or followed by
  VARIATION SELECTOR-16) goes to the first candidate with colour glyphs, when
  there is one: plain fonts often carry black outline versions of ❤ ☺ ✈.
- Whitespace stays with the face before it, so a run is not split at spaces.
- Neighbouring clusters with the same face form one run.

Not in here: building the preference order (the backends) or drawing (render.py).
Called by: fontconfig.py, windows.py.
"""
from __future__ import annotations

from typing import Callable, Iterable

import regex

from .faces import Face, has_color_glyphs

# Unicode's own emoji presentation: drawn as emoji by default, or followed by U+FE0F.
_EMOJI = regex.compile(r"\p{Emoji_Presentation}|️")


def segment(text: str, candidates: Callable[[str], Iterable[Face]], fallback: Face) -> list[tuple[int, int, Face]]:
    """[(start, end, face)] runs; candidates(ch) yields faces that have `ch`, best first."""
    spans: list[tuple[int, int, Face]] = []
    for match in regex.finditer(r"\X", text):
        cluster = match.group()
        if cluster.isspace() and spans:
            face = spans[-1][2]
        elif _EMOJI.search(cluster):
            options = list(candidates(cluster[0]))
            face = next((f for f in options if has_color_glyphs(f)), options[0] if options else fallback)
        else:
            face = next(iter(candidates(cluster[0])), fallback)
        if spans and spans[-1][2] == face:
            spans[-1] = (spans[-1][0], match.end(), face)
        else:
            spans.append((match.start(), match.end(), face))
    return spans
