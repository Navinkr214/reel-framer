"""layout.py: frame sizing, banner sizing, arrangement and caption positions."""
from __future__ import annotations

import itertools
import random

import pytest

from reel_framer.encoder import CHROMA_ALIGN
from reel_framer.layout import (
    LayoutError, Rect, Size, arrange, banner_size, caption_position, frame_size, parse_aspect,
)


def test_frame_derives_from_source_by_default():
    assert frame_size(Size(720, 1280), "source", 0) == Size(720, 1280)


def test_frame_shape_uses_source_short_side():
    assert frame_size(Size(1920, 1080), "9:16", 0) == Size(1080, 1920)
    assert frame_size(Size(1080, 1920), "16:9", 0) == Size(1920, 1080)


def test_frame_short_side_override():
    assert frame_size(Size(720, 1280), "9:16", 1080) == Size(1080, 1920)


def test_frame_is_chroma_aligned_for_odd_sources():
    frame = frame_size(Size(721, 1279), "source", 0)
    assert frame.w % CHROMA_ALIGN == 0 and frame.h % CHROMA_ALIGN == 0


@pytest.mark.parametrize("bad", ["9-16", "0:16", "a:b", "9:", ""])
def test_bad_aspect_raises(bad):
    with pytest.raises(LayoutError):
        parse_aspect(bad)


def test_banner_spans_width_at_its_own_aspect():
    assert banner_size(Size(1080, 1920), Size(1080, 300), max_h=480) == Size(1080, 300)
    assert banner_size(Size(1080, 1920), Size(540, 150), max_h=480) == Size(1080, 300)


def test_banner_taller_than_cap_shrinks_keeping_aspect():
    assert banner_size(Size(1080, 1920), Size(1000, 1000), max_h=480) == Size(480, 480)


def _check_invariants(layout, frame):
    blocks = [r for r in (layout.top_banner, layout.top_band, layout.video, layout.bottom_band,
                          layout.bottom_banner) if r is not None]
    for r in blocks:
        assert 0 <= r.x and r.right <= frame.w and 0 <= r.y and r.bottom <= frame.h, r
        assert all(v % CHROMA_ALIGN == 0 for v in (r.x, r.y, r.w, r.h)), r
    for upper, lower in zip(blocks, blocks[1:]):  # listed top to bottom: never overlapping
        assert upper.bottom <= lower.y, (upper, lower)


def test_edges_puts_banners_at_frame_edges_and_centres_video():
    frame = Size(1080, 1920)
    lay = arrange(frame, Size(1080, 1920), top_banner=Size(1080, 300), bottom_banner=Size(1080, 200),
                  banner_max_pct=25, video_scale_pct=100, stack="edges")
    assert lay.top_banner == Rect(0, 0, 1080, 300)
    assert lay.bottom_banner == Rect(0, 1720, 1080, 200)
    room = 1920 - 300 - 200
    assert lay.video.h == room and lay.video.y == 300
    assert abs((lay.video.x) - (frame.w - lay.video.right)) <= CHROMA_ALIGN
    _check_invariants(lay, frame)


def test_hug_makes_blocks_touch_the_video():
    frame = Size(1080, 1920)
    lay = arrange(frame, Size(1920, 1080), top_banner=Size(1080, 200), bottom_banner=Size(1080, 200),
                  banner_max_pct=25, video_scale_pct=100, stack="hug")
    assert lay.top_banner.bottom == lay.video.y
    assert lay.video.bottom == lay.bottom_banner.y
    top_gap, bottom_gap = lay.top_banner.y, frame.h - lay.bottom_banner.bottom
    assert abs(top_gap - bottom_gap) <= CHROMA_ALIGN
    _check_invariants(lay, frame)


def test_bands_sit_between_banner_and_video():
    frame = Size(1080, 1920)
    lay = arrange(frame, Size(1080, 1920), top_banner=Size(1080, 200), bottom_banner=Size(1080, 200),
                  top_band_h=120, bottom_band_h=100, banner_max_pct=25, video_scale_pct=100, stack="edges")
    assert lay.top_band == Rect(0, 200, 1080, 120)
    assert lay.bottom_band.bottom == lay.bottom_banner.y
    _check_invariants(lay, frame)


def test_no_room_for_video_raises():
    with pytest.raises(LayoutError):
        arrange(Size(1080, 1920), Size(1080, 1920), top_banner=Size(100, 100), bottom_banner=Size(100, 100),
                top_band_h=600, bottom_band_h=600, banner_max_pct=50, video_scale_pct=100, stack="edges")


def test_random_layouts_keep_invariants():
    rng = random.Random(7)
    for _ in range(300):
        source = Size(rng.randrange(64, 4000), rng.randrange(64, 4000))
        frame = frame_size(source, rng.choice(["source", "9:16", "4:5", "1:1", "16:9"]), rng.choice([0, 720, 1080]))
        banners = [Size(rng.randrange(16, 3000), rng.randrange(16, 3000)) if rng.random() < 0.8 else None
                   for _ in range(2)]
        try:
            lay = arrange(frame, source, top_banner=banners[0], bottom_banner=banners[1],
                          top_band_h=rng.choice([0, 2 * rng.randrange(0, 100)]),
                          bottom_band_h=rng.choice([0, 2 * rng.randrange(0, 100)]),
                          banner_max_pct=rng.uniform(5, 60), video_scale_pct=rng.uniform(30, 100),
                          stack=rng.choice(["edges", "hug"]))
        except LayoutError:
            continue
        _check_invariants(lay, frame)


@pytest.mark.parametrize("align, anchor", list(itertools.product(["left", "center", "right"], ["top", "center", "bottom"])))
def test_caption_position_stays_inside_zone(align, anchor):
    zone, cap, margin = Rect(100, 200, 800, 300), Size(400, 100), 40
    x, y = caption_position(zone, cap, align, margin, anchor)
    assert zone.x <= x and x + cap.w <= zone.right
    assert zone.y <= y and y + cap.h <= zone.bottom
    if align == "left":
        assert x == zone.x + margin
    if anchor == "bottom":
        assert y + cap.h <= zone.bottom - margin
