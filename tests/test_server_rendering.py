"""Rendering behaviour that matters on a server: thread limits under a CPU quota,
one render at a time, no ffmpeg left behind, speed presets, square pixels."""
from __future__ import annotations

import dataclasses
import subprocess
import threading
import time
from pathlib import Path

import pytest

from reel_framer import compose, encoder, pipeline
from reel_framer.media_probe import probe
from reel_framer.settings import Settings
from reel_framer.text import backend, fontconfig
from reel_framer.text.faces import has_color_glyphs

from .conftest import make_video, processes_mentioning


def _plan(tmp_path, seconds=1, **overrides):
    source = make_video(tmp_path / f"src-{seconds}.mp4", 320, 568, seconds)
    s = Settings()
    for key, value in overrides.items():
        setattr(s, key, value)
    return pipeline.build_plan(source, s, tmp_path), s, source


def test_thread_limit_reaches_decoders_filters_and_encoder(tmp_path):
    plan, _, _ = _plan(tmp_path)
    limited = dataclasses.replace(plan, threads=2)
    cmd = compose.video_command(limited, tmp_path / "o.mp4")
    inputs = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert all(cmd[i - 2:i] == ["-threads", "2"] for i in inputs), cmd
    assert cmd[cmd.index("-filter_complex_threads") + 1] == "2"
    after_map = cmd[cmd.index("-map"):]
    assert after_map[after_map.index("-threads") + 1] == "2"
    assert "-threads" not in compose.video_command(plan, tmp_path / "o.mp4")  # no quota: ffmpeg decides


def test_render_with_one_thread_works(tmp_path):
    plan, _, _ = _plan(tmp_path)
    out = tmp_path / "one-thread.mp4"
    compose.run_video(dataclasses.replace(plan, threads=1), out, tmp_path / "log.txt")
    assert probe(out).duration


def test_blur_render_keeps_square_pixels(tmp_path):
    plan, _, _ = _plan(tmp_path, background="blur", frame_aspect="9:16", video_scale_pct=70)
    out = tmp_path / "blur.mp4"
    compose.run_video(plan, out, tmp_path / "log.txt")
    sar = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=sample_aspect_ratio", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    assert sar in ("1:1", "N/A", "0:1"), sar
    info = probe(out)
    assert (info.width, info.height) == (plan.layout.frame.w, plan.layout.frame.h)


def test_abandoned_render_leaves_no_ffmpeg_running(tmp_path):
    plan, _, _ = _plan(tmp_path, seconds=20)
    out = tmp_path / "abandoned-render-marker.mp4"

    class Stop(Exception):
        pass

    def give_up(fraction):
        raise Stop

    with pytest.raises(Stop):
        compose.run_video(plan, out, tmp_path / "log.txt", give_up)
    assert processes_mentioning(out.name) == []



def test_renders_take_turns_and_the_second_says_it_is_waiting(tmp_path, monkeypatch):
    _, settings, source = _plan(tmp_path)
    first_started, release_first = threading.Event(), threading.Event()
    order: list[str] = []

    def fake_run(plan, output, log_path, on_progress=None):
        name = threading.current_thread().name
        order.append(f"{name} start")
        if name == "first":
            first_started.set()
            assert release_first.wait(timeout=30)
        output.write_bytes(b"done")
        order.append(f"{name} end")

    monkeypatch.setattr(compose, "run_video", fake_run)
    stages: dict[str, list[str]] = {"first": [], "second": []}

    def job(name):
        pipeline.process_file(source, settings, lambda stage, f: stages[name].append(stage))

    first = threading.Thread(target=job, args=("first",), name="first")
    first.start()
    assert first_started.wait(timeout=30)
    second = threading.Thread(target=job, args=("second",), name="second")
    second.start()
    deadline = time.monotonic() + 30
    while "Waiting for the video ahead to finish" not in stages["second"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "Waiting for the video ahead to finish" in stages["second"]
    assert order == ["first start"]  # the second has not started rendering
    release_first.set()
    first.join(30)
    second.join(30)
    assert order == ["first start", "first end", "second start", "second end"]


def test_speed_preset_is_passed_only_when_the_encoder_knows_it(tmp_path):
    info = probe(make_video(tmp_path / "p.mp4", 320, 568, 1))
    known = encoder.presets()
    if not known:
        pytest.skip("this ffmpeg's H.264 encoder has no x264 presets")
    args = encoder.video_args(info, 320, 568, "match", 18, known[0])
    assert args[args.index("-preset") + 1] == known[0]
    assert "-preset" not in encoder.video_args(info, 320, 568, "match", 18, "no-such-preset")
    assert "-preset" not in encoder.video_args(info, 320, 568, "match", 18, "")


def test_fontconfig_prefers_colour_faces_for_emoji(monkeypatch):
    if not fontconfig.available():
        pytest.skip("fontconfig not installed")
    monkeypatch.setenv("REEL_FRAMER_FONT_BACKEND", "fontconfig")
    base = backend.base_from_setting("", True)
    text = "Love ❤️ 🔥"
    spans = backend.segment(text, base, 64)
    emoji_faces = {face for start, end, face in spans if any(ch in text[start:end] for ch in "❤🔥")}
    chain = fontconfig._chain(fontconfig._pattern(base))
    if not any(has_color_glyphs(face) for face, _, _ in chain):
        pytest.skip("fontconfig knows no colour emoji font here")
    assert emoji_faces and all(has_color_glyphs(face) for face in emoji_faces)
