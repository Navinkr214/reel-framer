"""End to end: synthetic source + banners + captions -> MP4, checked by probing
the output and sampling pixels at known places and times."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageSequence

from reel_framer import assets, compose, encoder, pipeline
from reel_framer.media_probe import probe
from reel_framer.settings import Settings

from .conftest import animated_gif, make_video, nearest, pixel, solid_png

TOP = (250, 200, 30)
BG = (20, 200, 200)
GIF_COLOURS = {"red": (220, 30, 60), "blue": (30, 60, 220), "green": (30, 180, 90)}
GIF_FRAME_MS = 400


def _settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings()
    s.top_banner = assets.save_banner("top.png", solid_png(tmp_path / "top.png", 1080, 300, TOP).read_bytes())
    gif = animated_gif(tmp_path / "bottom.gif", 1080, 220, list(GIF_COLOURS.values()), GIF_FRAME_MS, loop=False)
    s.bottom_banner = assets.save_banner("bottom.gif", gif.read_bytes())
    s.frame_aspect = "9:16"
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _centre(rect) -> tuple[int, int]:
    return rect.x + rect.w // 2, rect.y + rect.h // 2


def test_full_render(tmp_path):
    source = make_video(tmp_path / "src.mp4", 720, 1280, 4)
    s = _settings(tmp_path, background="color", background_color="#14C8C8", video_scale_pct=80)
    s.top_text.text = "Wait for it… देखो 😂"
    plan = pipeline.build_plan(source, s, tmp_path)
    result = pipeline.process_file(source, s)

    out, src = probe(result.output), probe(source)
    frame = plan.layout.frame
    assert (out.width, out.height) == (frame.w, frame.h)
    assert abs(out.duration - src.duration) <= 1 / float(src.fps)
    assert out.video_codec == "h264" and out.audio_codec == "aac"

    palette = {"top": TOP, "bg": BG, **GIF_COLOURS}
    top_xy = (plan.layout.top_banner.x + 4, plan.layout.top_banner.y + 4)  # banner corner, clear of the caption
    bottom_xy = _centre(plan.layout.bottom_banner)
    gap_xy = (plan.layout.video.x // 2, _centre(plan.layout.video)[1])  # left of the video
    cycle = len(GIF_COLOURS) * GIF_FRAME_MS / 1000
    names = list(GIF_COLOURS)
    # Times inside each GIF frame, across several cycles: the play-once GIF must loop to the end.
    for t in [0.2, 0.6, 1.0, 1.4, 2.2, 2.6, 3.4, 3.8]:
        expected = names[int((t % cycle) / (GIF_FRAME_MS / 1000))]
        assert nearest(pixel(result.output, t, *bottom_xy), palette) == expected, t
        assert nearest(pixel(result.output, t, *top_xy), palette) == "top", t
        assert nearest(pixel(result.output, t, *gap_xy), palette) == "bg", t


def test_blur_background_shows_the_video_not_a_flat_colour(tmp_path):
    source = make_video(tmp_path / "src.mp4", 720, 1280, 1)
    s = _settings(tmp_path, background="blur", video_scale_pct=70)
    png = pipeline.preview(source, s, 0.5)
    (tmp_path / "p.png").write_bytes(png)
    image = Image.open(tmp_path / "p.png").convert("RGB")
    plan = pipeline.build_plan(source, s, tmp_path)
    x = plan.layout.video.x // 2
    column = {image.getpixel((x, y)) for y in range(plan.layout.video.y, plan.layout.video.bottom, 50)}
    assert len(column) > 1  # a blurred picture varies; a pad colour would not
    assert image.size == (plan.layout.frame.w, plan.layout.frame.h)


def test_rotated_phone_video_is_framed_upright(tmp_path):
    flat = make_video(tmp_path / "flat.mp4", 1280, 720, 1)
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(flat),
                    "-c", "copy", str(rotated)], check=True)
    assert (probe(rotated).width, probe(rotated).height) == (720, 1280)
    s = Settings()  # frame "source": must follow the DISPLAY shape
    result = pipeline.process_file(rotated, s)
    out = probe(result.output)
    assert (out.width, out.height) == (720, 1280)


def test_clip_banner_loops_past_its_own_length(tmp_path):
    source = make_video(tmp_path / "src.mp4", 720, 1280, 3)
    clip = make_video(tmp_path / "clip.mp4", 720, 120, 1, audio=False, source="color=c=0x1E3CDC")
    s = Settings()
    s.bottom_banner = assets.save_banner("clip.mp4", clip.read_bytes())
    plan = pipeline.build_plan(source, s, tmp_path)
    assert plan.overlays[0].animated
    result = pipeline.process_file(source, s)
    xy = _centre(plan.layout.bottom_banner)
    palette = {"clip": (30, 60, 220), "black": (0, 0, 0), "white": (255, 255, 255)}
    for t in (0.5, 1.5, 2.8):
        assert nearest(pixel(result.output, t, *xy), palette) == "clip", t


def test_caption_placements(tmp_path):
    source = make_video(tmp_path / "src.mp4", 720, 1280, 1)
    s = _settings(tmp_path)
    s.top_text.text, s.top_text.placement = "Band caption", "band"
    s.bottom_text.text, s.bottom_text.placement = "On the video", "over_video"
    plan = pipeline.build_plan(source, s, tmp_path)
    lay = plan.layout
    assert lay.top_band is not None and lay.top_banner.bottom == lay.top_band.y
    captions = [o for o in plan.overlays if not o.scaled]
    band_cap, video_cap = captions
    assert lay.top_band.y <= band_cap.rect.y and band_cap.rect.y + band_cap.rect.h <= lay.top_band.bottom
    assert lay.video.y <= video_cap.rect.y and video_cap.rect.y + video_cap.rect.h <= lay.video.bottom
    assert video_cap.rect.y > lay.video.y + lay.video.h // 2  # bottom caption sits at the video's bottom


def test_ffmpeg_failure_reports_its_log(tmp_path, monkeypatch):
    source = make_video(tmp_path / "src.mp4", 320, 568, 1)
    monkeypatch.setattr(encoder, "video_args", lambda *a, **k: ["-c:v", "no_such_encoder"])
    with pytest.raises(compose.ComposeError) as err:
        pipeline.process_file(source, Settings())
    log = Path(str(err.value).rsplit("full log: ", 1)[-1].rstrip(")"))
    assert log.is_file() and "no_such_encoder" in log.read_text()


def test_render_failing_midway_removes_the_partial_file(tmp_path, home, monkeypatch):
    source = make_video(tmp_path / "src.mp4", 320, 568, 1)

    def half_written(plan, output, log_path, on_progress=None):
        output.write_bytes(b"partial mp4")
        raise compose.ComposeError("stopped half way")

    monkeypatch.setattr(compose, "run_video", half_written)
    with pytest.raises(compose.ComposeError):
        pipeline.process_file(source, Settings())
    assert not list((home / "output").glob("*"))


def test_animated_webp_banner_is_converted_so_ffmpeg_sees_every_frame(tmp_path):
    frames = [Image.new("RGBA", (200, 60), c + (255,)) for c in GIF_COLOURS.values()]
    webp = tmp_path / "sticker.webp"
    frames[0].save(webp, save_all=True, append_images=frames[1:], duration=GIF_FRAME_MS, loop=0)
    stored = assets.save_banner("sticker.webp", webp.read_bytes())
    decoded = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames", "-of", "csv=p=0", str(assets.path_of(stored))],
        capture_output=True, text=True).stdout.strip()
    assert decoded == str(len(frames))


def test_still_image_as_source_is_refused_with_a_reason(tmp_path):
    still = solid_png(tmp_path / "photo.png", 720, 1280, TOP)
    with pytest.raises(pipeline.JobError, match="still image"):
        pipeline.process_file(still, Settings())


def test_animated_check_counts_video_packets_not_audio(tmp_path):
    # Real Instagram downloads (VP9 + AAC merged into MKV) can start with audio packets;
    # a check that counted every stream's packets called such a reel a still image.
    video = make_video(tmp_path / "v.mp4", 64, 64, 2, audio=False)
    audio_first = tmp_path / "audio_first.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-itsoffset", "0.5", "-i", str(video),
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-map", "1:a", "-map", "0:v", "-c:v", "copy", "-c:a", "aac", str(audio_first)], check=True)
    assert probe(audio_first, check_animated=True).animated is True
    result = pipeline.process_file(audio_first, Settings())
    assert probe(result.output).duration


def test_animated_check_tells_stills_from_motion(tmp_path):
    assert probe(solid_png(tmp_path / "s.png", 64, 64, TOP), check_animated=True).animated is False
    gif = animated_gif(tmp_path / "a.gif", 64, 64, list(GIF_COLOURS.values()), GIF_FRAME_MS, loop=False)
    assert probe(gif, check_animated=True).animated is True
    assert probe(make_video(tmp_path / "v.mp4", 64, 64, 1, audio=False), check_animated=True).animated is True
    assert probe(gif).animated is None  # not checked unless asked


def test_sideways_phone_jpeg_banner_is_turned_upright(tmp_path):
    image = Image.new("RGB", (400, 100), (200, 50, 50))
    exif = image.getexif()
    exif[0x0112] = 6  # EXIF Orientation: rotate 90 degrees clockwise to display
    path = tmp_path / "phone.jpg"
    image.save(path, exif=exif)
    stored = assets.save_banner("phone.jpg", path.read_bytes())
    assert Image.open(assets.path_of(stored)).size == (100, 400)


def test_gif_kept_as_uploaded(tmp_path):
    gif = animated_gif(tmp_path / "g.gif", 100, 40, list(GIF_COLOURS.values()), GIF_FRAME_MS, loop=True)
    stored = assets.save_banner("g.gif", gif.read_bytes())
    assert assets.path_of(stored).read_bytes() == gif.read_bytes()
    assert len(list(ImageSequence.Iterator(Image.open(assets.path_of(stored))))) == len(GIF_COLOURS)
