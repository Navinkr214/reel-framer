"""text/: per-script face selection, wrapping, fitting and colour emoji."""
from __future__ import annotations

import io

import pytest
from fontTools.ttLib import TTFont

from reel_framer import assets
from reel_framer.settings import TextStyle
from reel_framer.text import backend, fontconfig
from reel_framer.text.faces import covers
from reel_framer.text.render import CaptionError, render

FRAME_W = 1080
MIXED = "Follow करें 🔥 for more"

needs_font_service = pytest.mark.skipif(backend.name() == "none", reason="no CoreText / fontconfig")


def test_complex_script_shaping_is_available():
    # Without raqm (and the FriBiDi library it loads) Indic text is drawn unshaped: wrong conjuncts.
    from PIL import features

    assert features.check_feature("raqm"), "Pillow cannot shape text here: FriBiDi is missing"


def test_blank_caption_is_none():
    assert render("  \n ", TextStyle(), FRAME_W, max_w=FRAME_W) is None


@needs_font_service
def test_each_script_gets_a_face_that_has_its_glyphs():
    base = backend.base_from_setting("", True)
    spans = backend.segment(MIXED, base, 64)
    assert spans[0][0] == 0 and spans[-1][1] == len(MIXED)
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:])), "runs must tile the text"
    for start, end, face in spans:
        first = MIXED[start:end].strip()[:1]
        if first:
            assert covers(face, first), (MIXED[start:end], face)
    faces_used = {face for _, _, face in spans}
    assert len(faces_used) >= 3  # Latin, Devanagari and emoji need different faces


@pytest.mark.skipif(not fontconfig.available(), reason="fontconfig not installed")
def test_fontconfig_backend_covers_mixed_scripts(monkeypatch):
    monkeypatch.setenv("REEL_FRAMER_FONT_BACKEND", "fontconfig")
    spans = backend.segment("Hello नमस्ते", backend.base_from_setting("", False), 64)
    assert spans[-1][1] == len("Hello नमस्ते")
    for start, end, face in spans:
        first = "Hello नमस्ते"[start:end].strip()[:1]
        if first:
            assert covers(face, first)


def test_long_text_wraps_within_width():
    one_line = render("Short", TextStyle(), FRAME_W, max_w=FRAME_W).image
    long = render("This caption is long enough that it has to wrap onto several lines " * 2,
                  TextStyle(), FRAME_W, max_w=600).image
    assert long.width <= 600
    assert long.height > 2 * one_line.height


def test_explicit_line_breaks_are_kept():
    one = render("Line", TextStyle(), FRAME_W, max_w=FRAME_W).image
    two = render("Line\nLine", TextStyle(), FRAME_W, max_w=FRAME_W).image
    assert two.height > one.height * 1.5


def test_caption_shrinks_to_fit_height():
    style = TextStyle(size_pct=10)
    caption = render("A caption that is too big for a short banner", style, FRAME_W, max_w=1000, max_h=100)
    assert caption.image.height <= 100 and caption.image.width <= 1000
    assert caption.font_px < FRAME_W * style.size_pct / 100


def test_word_without_spaces_breaks_between_clusters():
    caption = render("नमस्ते" * 12, TextStyle(), FRAME_W, max_w=400)
    assert caption.image.width <= 400


def test_impossible_fit_raises():
    with pytest.raises(CaptionError):
        render("x", TextStyle(), FRAME_W, max_w=0, max_h=10)


@needs_font_service
def test_emoji_is_drawn_in_colour():
    # White text with a black outline is grey everywhere; only a colour emoji adds colour.
    plain = render("Fire", TextStyle(), FRAME_W, max_w=FRAME_W).image
    emoji = render("Fire 🔥", TextStyle(), FRAME_W, max_w=FRAME_W).image

    def coloured(image):
        data, step = image.tobytes(), len(image.getbands())  # RGBA bytes, one pixel per step
        return sum(1 for i in range(0, len(data), step)
                   if data[i + 3] and not (data[i] == data[i + 1] == data[i + 2]))

    assert coloured(plain) == 0
    assert coloured(emoji) > 0


@needs_font_service
def test_emoji_follows_the_font_size_and_is_not_clipped():
    # Bitmap emoji exist only at fixed strike sizes. Drawn at a bigger strike without scaling
    # down, the emoji overflows its line box and gets cut off at the image edge.
    image = render("🔥", TextStyle(size_pct=100 * 100 / FRAME_W, outline_pct=10), FRAME_W, max_w=FRAME_W).image
    data, step = image.tobytes(), len(image.getbands())
    coloured = [(i // step % image.width, i // step // image.width) for i in range(0, len(data), step)
                if data[i + 3] and not (data[i] == data[i + 1] == data[i + 2])]
    xs, ys = [x for x, _ in coloured], [y for _, y in coloured]
    assert coloured
    assert min(xs) > 0 and min(ys) > 0 and max(xs) < image.width - 1 and max(ys) < image.height - 1


def test_box_fills_the_caption_background():
    image = render("Box", TextStyle(box_opacity_pct=100, box_color="#123456"), FRAME_W, max_w=FRAME_W).image
    centre_left = image.getpixel((image.width // 20, image.height // 2))
    assert centre_left == (0x12, 0x34, 0x56, 255)


@needs_font_service
def test_uploaded_font_draws_the_text_it_covers():
    # A copy of the OS UI font renamed, so the OS cannot substitute its installed original.
    source = backend.face_for(backend.base_from_setting("", False), 64)
    font = TTFont(source.path, fontNumber=source.index or 0)
    for record in font["name"].names:
        if record.nameID in (1, 3, 4, 6, 16):  # family, unique id, full name, PostScript name, typo family
            record.string = "ReelFramerTestFont"
    data = io.BytesIO()
    font.save(data)
    stored = assets.save_font("ReelFramerTestFont.ttf", data.getvalue())
    spans = backend.segment("Abc", backend.base_from_setting(f"asset:{stored}", False), 64)
    assert [face.path for _, _, face in spans] == [str(assets.path_of(stored))]
    assert render("Abc", TextStyle(font=f"asset:{stored}"), FRAME_W, max_w=FRAME_W) is not None
