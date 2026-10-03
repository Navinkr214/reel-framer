"""The Create tab on the real app (AppTest): the Create button takes the first click.

A typed or pasted link reaches the script only when its box loses focus (or on Ctrl+Enter),
and clicking the button is what takes the focus away; a button greyed out until the link
had arrived swallowed that click. desktop/ui_check.py checks the same in a real browser.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from reel_framer import pipeline
from reel_framer.downloader import DownloadError

APP = str(Path(__file__).resolve().parent.parent / "app.py")
LINK = "https://example.invalid/reel.mp4"


@pytest.fixture(autouse=True)
def local_app(monkeypatch):
    """The app as it runs on a computer, open without a password (the Docker image sets
    REEL_FRAMER_HOSTED, and a hosted copy without APP_PASSWORD stays locked)."""
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)


def _create_button(at: AppTest):
    return next(b for b in at.button if b.label == "Create videos")


def test_create_button_is_clickable_before_any_link_arrives():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert _create_button(at).disabled is False


def test_create_with_nothing_given_says_what_is_missing():
    at = AppTest.from_file(APP, default_timeout=60).run()
    _create_button(at).click().run()
    assert any("Paste a link" in warning.value for warning in at.warning)


def test_one_click_runs_the_typed_link(monkeypatch):
    asked: list[str] = []

    def download(link, settings, report):
        asked.append(link)
        raise DownloadError("no network in this test")

    monkeypatch.setattr(pipeline, "download_link", download)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_area(key="links").input(LINK)
    _create_button(at).click().run()
    assert asked == [LINK]
    assert any(LINK in error.value for error in at.error)
