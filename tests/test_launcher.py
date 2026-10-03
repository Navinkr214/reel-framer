"""desktop/launcher.py from the source tree: the private server starts, answers, and
stops with nothing left behind; a failed start is reported; an updated yt-dlp wins."""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from .conftest import processes_mentioning

LAUNCHER = Path(__file__).resolve().parent.parent / "desktop" / "launcher.py"


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_file_location("reel_framer_launcher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_server_starts_answers_and_stops_with_nothing_left(launcher, tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_FRAMER_HOME", str(tmp_path / "home"))
    port = launcher.free_port()
    url = f"http://127.0.0.1:{port}"
    server = launcher.start_server(port, tmp_path / "server.log")
    try:
        assert launcher.wait_until_ready(server, url), (tmp_path / "server.log").read_text()
        with urllib.request.urlopen(url) as page:
            assert page.status == 200 and b"<html" in page.read().lower()
    finally:
        launcher.stop_server(server)
    assert server.poll() is not None
    assert processes_mentioning(f"--serve {port}") == []
    log = (tmp_path / "server.log").read_text(encoding="utf-8")
    # A server that crashes on its way out leaves a crash report on every quit of the app
    # (macOS also offers to reopen it): a normal stop must end without one.
    assert "Fatal Python error" not in log and server.returncode != -signal.SIGABRT, log
    assert "warm-up:" in log  # the slow first-use work ran before the server answered


def test_a_server_that_cannot_start_is_reported(launcher, tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_FRAMER_HOME", str(tmp_path / "home"))
    port = 65536  # one past the last TCP port: no server can listen there, on any system
    server = launcher.start_server(port, tmp_path / "server.log")
    try:
        assert launcher.wait_until_ready(server, f"http://127.0.0.1:{port}") is False
    finally:
        launcher.stop_server(server)
    assert (tmp_path / "server.log").read_text().strip()


def test_updated_yt_dlp_is_imported_before_the_bundled_one(tmp_path):
    home = tmp_path / "home"
    package = home / "python" / "yt_dlp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("UPDATED = True\n")
    code = ("import importlib.util, sys;"
            f"spec = importlib.util.spec_from_file_location('l', {str(LAUNCHER)!r});"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
            "m.prefer_updated_packages(); import yt_dlp; print(yt_dlp.__file__)")
    env = {**os.environ, "REEL_FRAMER_HOME": str(home)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout
    assert Path(out.strip()) == package / "__init__.py"


def test_server_stops_by_itself_when_the_window_process_dies(tmp_path):
    """Quit, Force Quit or a crash of the window process must not leave the server running."""
    parent = (
        "import importlib.util, os, sys, urllib.request;"
        f"spec = importlib.util.spec_from_file_location('l', {str(LAUNCHER)!r});"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "port = m.free_port(); url = f'http://127.0.0.1:{port}';"
        f"server = m.start_server(port, __import__('pathlib').Path({str(tmp_path / 'server.log')!r}));"
        "assert m.wait_until_ready(server, url);"
        "print(port, server.pid, flush=True);"
        "os._exit(0)"  # die abruptly: no cleanup code runs
    )
    env = {**os.environ, "REEL_FRAMER_HOME": str(tmp_path / "home")}
    out = subprocess.run([sys.executable, "-c", parent], capture_output=True, text=True, env=env, check=True)
    port, pid = out.stdout.split()
    deadline = time.monotonic() + 30
    while processes_mentioning(f"--serve {port}") and time.monotonic() < deadline:
        time.sleep(0.1)
    survivors = processes_mentioning(f"--serve {port}")
    for survivor in survivors:  # a failing run must not leave a server behind either
        os.kill(int(survivor), signal.SIGTERM)
    assert survivors == [], "the server outlived its window process"
