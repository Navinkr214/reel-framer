"""Shared test helpers: an isolated app home folder per test, and synthetic media
made with ffmpeg's test sources and Pillow (no network, no real reels).

Pixel checks classify a sampled colour to the NEAREST colour of a known palette
instead of using a tolerance, so lossy H.264 colour shifts cannot flip a result
unless the colour really changed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setenv("REEL_FRAMER_HOME", str(path))
    return path


def make_video(path: Path, w: int, h: int, seconds: float, *, rate: int = 30, audio: bool = True,
               source: str = "testsrc2", extra: tuple[str, ...] = ()) -> Path:
    joiner = ":" if "=" in source else "="  # "testsrc2" vs "color=c=red"
    lavfi = f"{source}{joiner}size={w}x{h}:rate={rate}:duration={seconds}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", lavfi]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    cmd += ["-pix_fmt", "yuv420p", *extra, str(path)]
    subprocess.run(cmd, check=True)
    return path


def solid_png(path: Path, w: int, h: int, rgb: tuple[int, int, int]) -> Path:
    Image.new("RGB", (w, h), rgb).save(path)
    return path


def animated_gif(path: Path, w: int, h: int, colours: list[tuple[int, int, int]], frame_ms: int, *, loop: bool) -> Path:
    frames = [Image.new("RGB", (w, h), c) for c in colours]
    extra = {"loop": 0} if loop else {}  # no loop field = play once
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=frame_ms, **extra)
    return path


def pixel(video: Path, t: float, x: int, y: int) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"crop=1:1:{x}:{y},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    return raw[0], raw[1], raw[2]


def nearest(colour: tuple[int, int, int], palette: dict[str, tuple[int, int, int]]) -> str:
    return min(palette, key=lambda name: sum((a - b) ** 2 for a, b in zip(colour, palette[name])))
