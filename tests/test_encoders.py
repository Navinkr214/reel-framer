"""encoder.py's encoder choice: discovery from the live ffmpeg build, renders with each
usable encoder, and the fall-back to the default when a chosen encoder fails."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from reel_framer import encoder, pipeline
from reel_framer.media_probe import probe
from reel_framer.settings import Settings

from .conftest import make_video

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_usable_encoders_include_the_default_and_take_the_output_pixel_format():
    encoders = encoder.video_encoders()
    assert encoder.encoder_name() in encoders
    for name in encoders:
        assert encoder.OUTPUT_PIX_FMT in encoder._pixel_formats(name)


def test_unknown_choice_falls_back_to_the_default():
    assert encoder.chosen_encoder("no-such-encoder") == encoder.encoder_name()
    assert encoder.chosen_encoder("") == encoder.encoder_name()


def test_every_usable_encoder_renders_video_with_sound(tmp_path):
    source = make_video(tmp_path / "src.mp4", 320, 568, 1)
    for name in encoder.video_encoders():
        result = pipeline.process_file(source, Settings(video_encoder=name))
        info = probe(result.output)
        assert info.video_codec == encoder.OUTPUT_VIDEO_CODEC and info.audio_codec, name


def test_a_failing_chosen_encoder_falls_back_and_says_so(tmp_path, monkeypatch):
    real = encoder.video_encoders()
    monkeypatch.setattr(encoder, "video_encoders", lambda: {**real, "fake_hardware": "Fake"})
    source = make_video(tmp_path / "src.mp4", 320, 568, 1)
    stages: list[str] = []
    result = pipeline.process_file(source, Settings(video_encoder="fake_hardware"),
                                   lambda stage, fraction: stages.append(stage))
    assert probe(result.output).video_codec == encoder.OUTPUT_VIDEO_CODEC
    assert any("fake_hardware" in stage and encoder.encoder_name() in stage for stage in stages)


def test_settings_offer_an_encoder_choice_only_when_there_is_one(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    default = encoder.encoder_name()
    monkeypatch.setattr(encoder, "video_encoders", lambda: {default: "Standard"})
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert "Encoder" not in [box.label for box in at.selectbox]
    monkeypatch.setattr(encoder, "video_encoders", lambda: {default: "Standard", "fake_hardware": "Fake"})
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert "Encoder" in [box.label for box in at.selectbox]
