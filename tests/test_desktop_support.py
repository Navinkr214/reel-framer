"""Pieces the packaged desktop app relies on: yt-dlp updates without pip, the visible
output folder, and showing a file in the computer's file manager."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from reel_framer import paths, pipeline, updater
from reel_framer.ui import widgets


def _wheel(version: str, extra: dict[str, str] | None = None) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("yt_dlp/__init__.py", "")
        archive.writestr("yt_dlp/version.py", f"__version__ = {version!r}\n")
        archive.writestr(f"yt_dlp-{version}.dist-info/METADATA", f"Name: yt-dlp\nVersion: {version}\n")
        for name, body in (extra or {}).items():
            archive.writestr(name, body)
    return data.getvalue()


def _pypi(monkeypatch, version: str, wheel: bytes, sha256: str | None = None) -> None:
    meta = {"info": {"version": version}, "urls": [
        {"packagetype": "sdist", "filename": f"yt_dlp-{version}.tar.gz", "url": "https://x/sdist",
         "digests": {"sha256": "0"}},
        {"packagetype": "bdist_wheel", "filename": f"yt_dlp-{version}-py3-none-any.whl", "url": "https://x/wheel",
         "digests": {"sha256": sha256 or hashlib.sha256(wheel).hexdigest()}},
    ]}

    def fetch(url: str) -> bytes:
        return json.dumps(meta).encode() if url == updater.PYPI_JSON else wheel

    monkeypatch.setattr(updater, "_fetch", fetch)


def test_update_installs_the_newest_wheel_and_replaces_an_earlier_one(monkeypatch):
    _pypi(monkeypatch, "2099.1.1", _wheel("2099.1.1"))
    assert updater.update() == "2099.1.1"
    _pypi(monkeypatch, "2099.2.2", _wheel("2099.2.2"))
    assert updater.update() == "2099.2.2"
    site = updater.site_dir()
    assert "2099.2.2" in (site / "yt_dlp" / "version.py").read_text()
    assert not (site / "yt_dlp-2099.1.1.dist-info").exists()
    assert [p.name for p in paths.home().iterdir() if p.name.startswith(".python")] == []


def test_checksum_mismatch_changes_nothing(monkeypatch):
    _pypi(monkeypatch, "2099.1.1", _wheel("2099.1.1"))
    updater.update()
    _pypi(monkeypatch, "2099.2.2", _wheel("2099.2.2"), sha256="0" * 64)
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.update()
    assert "2099.1.1" in (updater.site_dir() / "yt_dlp" / "version.py").read_text()


def test_unsafe_archive_entry_is_refused(monkeypatch):
    _pypi(monkeypatch, "2099.1.1", _wheel("2099.1.1", {"../escaped.py": "x = 1"}))
    with pytest.raises(updater.UpdateError, match="unsafe"):
        updater.update()
    assert not (paths.home() / "escaped.py").exists() and not updater.site_dir().exists()


def test_pypi_unreachable_explains(monkeypatch):
    def offline(url):
        raise OSError("network is unreachable")
    monkeypatch.setattr(updater, "_fetch", offline)
    with pytest.raises(updater.UpdateError, match="Could not reach PyPI"):
        updater.update()


def test_output_folder_from_the_desktop_app(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_FRAMER_OUTPUT_DIR", str(tmp_path / "Movies" / "Reel Framer"))
    assert paths.output_dir("") == tmp_path / "Movies" / "Reel Framer"
    assert paths.output_dir(str(tmp_path / "chosen")) == tmp_path / "chosen"  # Settings wins


def test_reveal_opens_the_platform_file_manager(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(widgets.subprocess, "run", lambda cmd, check: calls.append(cmd))
    target = tmp_path / "video.mp4"
    for platform, expected in [("darwin", ["open", "-R", str(target)]),
                               ("win32", ["explorer", f"/select,{target}"]),
                               ("linux", ["xdg-open", str(tmp_path)])]:
        monkeypatch.setattr(widgets.sys, "platform", platform)
        widgets.reveal(target)
        assert calls[-1] == expected


def test_output_names_fit_the_file_system_even_with_a_counter(tmp_path):
    limit = paths.max_name_length(tmp_path)
    first = pipeline._output_name("x" * (limit * 2), tmp_path)
    (tmp_path / first).touch()
    second = pipeline._output_name("x" * (limit * 2), tmp_path)
    assert first != second
    assert len(first.encode()) <= limit and len(second.encode()) <= limit
