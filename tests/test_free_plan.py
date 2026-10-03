"""Hosting without a disk (Render's free plan): starting settings from defaults/,
a host-provided login file, uploads streamed to disk, a preview that waits for a
click on a sub-one-CPU server and reads its video's length once, not on every refresh."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from reel_framer import assets, downloader, hosting, paths, pipeline, server_defaults
from reel_framer import settings as store
from reel_framer.settings import Settings
from reel_framer.ui import settings_tab

from .conftest import make_video, solid_png

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def defaults(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "defaults"
    monkeypatch.setenv("REEL_FRAMER_DEFAULTS", str(folder))
    return folder


def _configured(tmp_path) -> Settings:
    s = Settings(encoder_preset="veryfast", output_dir=str(tmp_path / "my-videos"),
                 cookies_browser="chrome")
    s.top_banner = assets.save_banner("top.png", solid_png(tmp_path / "top.png", 300, 80, (200, 30, 30)).read_bytes())
    s.cookies_file = assets.save_cookies("cookies.txt", b"# Netscape HTTP Cookie File\n")
    s.top_text.text = "Hello"
    return s


def test_saved_defaults_leave_out_logins_and_local_folders(tmp_path, defaults):
    s = _configured(tmp_path)
    old = defaults / "assets" / "stale.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"stale")
    server_defaults.save(s)
    saved = json.loads((defaults / "settings.json").read_text())
    assert saved["cookies_browser"] == "" and saved["cookies_file"] == "" and saved["output_dir"] == ""
    assert saved["top_banner"] == s.top_banner and saved["encoder_preset"] == "veryfast"
    assert sorted(p.name for p in (defaults / "assets").iterdir()) == [s.top_banner]


def test_hosted_fresh_server_starts_from_defaults(tmp_path, defaults, home, monkeypatch):
    s = _configured(tmp_path)
    server_defaults.save(s)
    # A fresh server: empty home folder.
    for file in paths.assets_dir().iterdir():
        file.unlink()
    paths.settings_file().unlink(missing_ok=True)
    monkeypatch.setenv("REEL_FRAMER_HOSTED", "1")
    assert server_defaults.seed_if_fresh() is True
    seeded = store.load()
    assert seeded.top_banner == s.top_banner and assets.exists(seeded.top_banner)
    assert seeded.top_text.text == "Hello" and seeded.cookies_file == ""
    assert server_defaults.seed_if_fresh() is False  # only when there are no settings yet


def test_local_copy_never_seeds(tmp_path, defaults, monkeypatch):
    server_defaults.save(_configured(tmp_path))
    paths.settings_file().unlink(missing_ok=True)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    assert server_defaults.seed_if_fresh() is False
    assert not paths.settings_file().exists()


def test_host_cookies_file_is_used_unless_a_file_was_uploaded(tmp_path, monkeypatch):
    host_file = tmp_path / "host-cookies.txt"
    host_file.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setenv("REEL_FRAMER_COOKIES_FILE", str(host_file))
    seen = []
    monkeypatch.setattr(downloader, "download",
                        lambda url, dest, **kw: seen.append(kw["cookies_file"]) or [])
    s = Settings()
    pipeline.download_link("https://example.com/v", s)
    s.cookies_file = assets.save_cookies("mine.txt", b"# Netscape HTTP Cookie File\n")
    pipeline.download_link("https://example.com/v", s)
    assert seen == [str(host_file), str(assets.path_of(s.cookies_file))]


def test_missing_host_cookies_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_FRAMER_COOKIES_FILE", str(tmp_path / "not-there.txt"))
    assert hosting.host_cookies_file() is None


def test_upload_is_streamed_named_by_content_and_deduplicated(tmp_path):
    data = make_video(tmp_path / "clip.mp4", 64, 64, 1).read_bytes()
    first = pipeline.save_upload("My Holiday Reel.mp4", io.BytesIO(data))
    second = pipeline.save_upload("My Holiday Reel.mp4", io.BytesIO(data))
    assert first == second and first.read_bytes() == data
    assert first.name.startswith("upload-My-Holiday-Reel-") and first.suffix == ".mp4"
    assert not list(paths.downloads_dir().glob(".upload-*"))  # no temporary leftovers


def test_preview_waits_for_a_click_on_a_fractional_cpu(tmp_path, monkeypatch):
    make_video(paths.downloads_dir() / "source.mp4", 320, 568, 1)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    monkeypatch.setattr(hosting, "fractional_cpu", lambda *a: True)
    at = AppTest.from_file(APP, default_timeout=60).run()
    toggle = next(t for t in at.toggle if t.label == "Redraw on every change")
    assert toggle.value is False
    assert "preview" not in at.session_state
    next(b for b in at.button if b.label == "Update the preview").click().run()
    assert at.session_state["preview"][1]  # PNG bytes were drawn


def test_preview_redraws_by_itself_with_whole_cpus(tmp_path, monkeypatch):
    make_video(paths.downloads_dir() / "source.mp4", 320, 568, 1)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    monkeypatch.setattr(hosting, "fractional_cpu", lambda *a: False)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.session_state["preview"][1]


def test_preview_reads_the_video_length_once_not_on_every_refresh(tmp_path, monkeypatch):
    # Every click anywhere on the page reruns the Settings tab too; an ffprobe run each time
    # cost seconds per click on a Windows laptop.
    make_video(paths.downloads_dir() / "source.mp4", 320, 568, 1)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    probed: list[Path] = []
    real_probe = settings_tab.probe
    monkeypatch.setattr(settings_tab, "probe", lambda path: probed.append(path) or real_probe(path))
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.run()  # a refresh with nothing changed
    assert len(probed) == 1


def test_shipped_defaults_load(monkeypatch):
    monkeypatch.delenv("REEL_FRAMER_DEFAULTS", raising=False)
    shipped = server_defaults.defaults_dir() / "settings.json"
    assert shipped.is_file()
    assert store.load(shipped).encoder_preset == "veryfast"
