"""downloader.py against a local HTTP server (yt-dlp's generic extractor), so the
download path is exercised without the internet or Instagram."""
from __future__ import annotations

import functools
import http.server
import sys
import threading

import pytest

from reel_framer import paths, pipeline
from reel_framer.downloader import DownloadError, download
from reel_framer.media_probe import probe
from reel_framer.settings import Settings

from .conftest import make_video


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


class _QuietServer(http.server.ThreadingHTTPServer):
    """Test server that stays quiet when a client hangs up early (yt-dlp does, after probing)."""

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


@pytest.fixture
def server(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    make_video(served / "clip.mp4", 320, 568, 1)
    handler = functools.partial(_QuietHandler, directory=str(served))
    httpd = _QuietServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_downloads_a_direct_video_link(server):
    seen = []
    items = download(f"{server}/clip.mp4", paths.downloads_dir(), on_progress=seen.append)
    assert len(items) == 1
    info = probe(items[0].path)
    assert (info.width, info.height) == (320, 568)
    assert items[0].path.parent == paths.downloads_dir()


def test_second_download_reuses_the_file(server):
    first = download(f"{server}/clip.mp4", paths.downloads_dir())[0].path
    stamp = first.stat().st_mtime_ns
    second = download(f"{server}/clip.mp4", paths.downloads_dir())[0].path
    assert second == first and second.stat().st_mtime_ns == stamp


def test_missing_video_gives_a_clean_error(server):
    with pytest.raises(DownloadError) as err:
        download(f"{server}/nothing-here.mp4", paths.downloads_dir())
    message = str(err.value)
    assert message and not message.startswith("ERROR") and "\x1b[" not in message


def test_link_to_framed_video(server):
    s = Settings()
    sources = pipeline.download_link(f"{server}/clip.mp4", s)
    result = pipeline.process_file(sources[0].path, s, title=sources[0].title)
    assert probe(result.output).duration
