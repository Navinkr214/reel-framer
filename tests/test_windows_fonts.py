"""The Windows font backend's own logic, run here with real font files. Only the three
Windows touch points are replaced (the registry's font list, the font-link list and the
UI font's name); the GitHub Windows job runs the real thing.

Font files are chosen by what they contain (via fontconfig, and the fonts' own tables),
not by name, so the same tests run on macOS and Linux."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from reel_framer.settings import TextStyle
from reel_framer.text import backend, windows
from reel_framer.text.faces import covers, has_color_glyphs
from reel_framer.text.render import render

pytestmark = pytest.mark.skipif(shutil.which("fc-match") is None, reason="needs fontconfig to find font files")


def _match(pattern: str) -> Path:
    return Path(subprocess.run(["fc-match", "--format", "%{file}", pattern], capture_output=True, text=True).stdout)


def _colour_emoji_files() -> list[Path]:
    files = {Path(line) for line in subprocess.run(["fc-list", "--format", "%{file}\n"], capture_output=True,
                                                   text=True).stdout.splitlines() if line}
    out = []
    for path in sorted(files):
        try:
            font = TTFont(str(path), lazy=True, fontNumber=0)
        except Exception:
            continue
        if any(tag in font for tag in ("sbix", "CBDT", "COLR")) and 0x1F525 in (font.getBestCmap() or {}):
            out.append(path)
        font.close()
    return out


@pytest.fixture
def fake_windows(monkeypatch, tmp_path):
    regular, bold = _match("sans-serif:weight=regular"), _match("sans-serif:weight=bold")
    devanagari = _match("sans-serif:charset=0915")
    every_devanagari = {Path(line) for line in subprocess.run(
        ["fc-list", ":charset=0915", "--format", "%{file}\n"], capture_output=True, text=True).stdout.splitlines() if line}
    emoji = _colour_emoji_files()
    files = sorted({regular, bold, devanagari, *every_devanagari, _match("sans-serif:charset=2764"), *emoji})
    ui_family = windows.build_index([regular], tmp_path / "probe.json")[0].family
    links: list[tuple[str, str]] = []
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "_registered_files", lambda: files)
    monkeypatch.setattr(windows, "_ui_family", lambda: ui_family)
    monkeypatch.setattr(windows, "_links", lambda family: links)
    monkeypatch.setenv("REEL_FRAMER_FONT_BACKEND", "windows")
    windows._built.cache_clear()
    windows._order.cache_clear()
    yield {"files": files, "devanagari": devanagari, "emoji": emoji, "links": links, "ui": ui_family}
    windows._built.cache_clear()
    windows._order.cache_clear()


def test_index_is_read_once_then_from_the_cache(tmp_path, fake_windows, monkeypatch):
    cache = tmp_path / "fonts.json"
    first = windows.build_index(fake_windows["files"], cache)
    monkeypatch.setattr(windows, "_read", lambda path: pytest.fail(f"read {path} again"))
    assert windows.build_index(fake_windows["files"], cache) == first


def test_ui_font_at_the_asked_weight(fake_windows):
    regular = windows.face_for(backend.base_from_setting("", False), 64)
    bold = windows.face_for(backend.base_from_setting("", True), 64)
    weights = {e.face: e.weight for e in windows._entries()}
    assert regular != bold and weights[bold] > weights[regular]
    assert {e.family for e in windows._entries() if e.face in (regular, bold)} == {fake_windows["ui"]}


def test_mixed_scripts_get_faces_that_have_them_and_emoji_in_colour(fake_windows):
    text = "Follow करें 🔥 for more ❤️"   # ❤ is also in plain fonts; with U+FE0F it must be colour
    spans = backend.segment(text, backend.base_from_setting("", True), 64)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for start, end, face in spans:
        first = text[start:end].strip()[:1]
        if first:
            assert covers(face, first), (text[start:end], face)
    if fake_windows["emoji"]:
        for emoji in ("🔥", "❤"):
            face = next(face for start, end, face in spans if emoji in text[start:end])
            assert has_color_glyphs(face), emoji


def test_windows_font_links_come_before_other_fonts(fake_windows):
    # A link names a family; take one the normal order does NOT use, so only the link can choose it.
    base = backend.base_from_setting("", True)

    def families(spans):
        return {e.family for e in windows._entries() if e.face in {face for _, _, face in spans}}

    normal = families(backend.segment("नमस्ते", base, 64))
    others = [e for e in windows._order("ui", "", True) if e.has(ord("न")) and e.family not in normal]
    if not others:
        pytest.skip("needs a second family with Devanagari to tell the link from the normal order")
    target = others[-1]
    fake_windows["links"].append((Path(target.face.path).name, target.family))
    windows._order.cache_clear()
    assert families(backend.segment("नमस्ते", base, 64)) == {target.family}


def test_captions_render_through_the_windows_backend(fake_windows):
    caption = render("Follow करें 🔥", TextStyle(), 1080, max_w=1000)
    assert caption is not None and caption.image.width <= 1000
