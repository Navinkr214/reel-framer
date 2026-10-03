"""settings.py: round trip, and tolerance of missing / malformed / foreign values."""
from __future__ import annotations

import json

from reel_framer import paths
from reel_framer import settings as store
from reel_framer.settings import Settings


def test_round_trip():
    s = Settings(frame_aspect="9:16", banner_max_pct=30.5)
    s.top_text.text = "नमस्ते 🔥"
    store.save(s)
    assert store.load() == s


def test_missing_file_gives_defaults():
    assert store.load() == Settings()


def test_corrupt_file_gives_defaults():
    paths.settings_file().write_text("{not json")
    assert store.load() == Settings()


def test_wrong_types_and_unknown_choices_fall_back_per_field():
    paths.settings_file().write_text(json.dumps({
        "frame_aspect": "4:5",          # kept
        "banner_max_pct": "big",        # wrong type -> default
        "stack": "diagonal",            # unknown choice -> default
        "crf": True,                    # bool is not a number here -> default
        "top_text": {"text": "hi", "align": "left", "size_pct": 7},
        "from_a_newer_version": 1,      # ignored
    }))
    s = store.load()
    assert s.frame_aspect == "4:5"
    assert s.banner_max_pct == Settings().banner_max_pct
    assert s.stack == Settings().stack
    assert s.crf == Settings().crf
    assert (s.top_text.text, s.top_text.align, s.top_text.size_pct) == ("hi", "left", 7.0)
