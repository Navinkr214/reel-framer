"""Files the user uploads in Settings: banners, caption fonts and cookies.txt.

Each upload is stored once in paths.assets_dir(), named after the original file
plus a hash of its content, so two uploads never collide and re-uploading the
same file reuses it. Banners are normalised so ffmpeg can show them for the
whole video:
- still images -> PNG with the EXIF orientation applied (phone JPEGs are often
  stored sideways plus an orientation tag that ffmpeg's image decoders ignore);
- animated images this ffmpeg build decodes completely (it decodes as many
  frames as Pillow counts) -> kept as uploaded (GIF, APNG);
- other animated images (e.g. animated WebP, which ffmpeg 7 cannot decode)
  -> APNG via Pillow, keeping every frame, its timing and its transparency;
- video clips -> kept as uploaded (ffmpeg loops them; their sound is not used).

Not in here: using the files (pipeline.py) or the Settings fields naming them.
Called by: ui/settings_tab.py, text/backend.py, pipeline.py.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
from pathlib import Path

import regex
from fontTools.ttLib import TTCollection, TTFont, TTLibError
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from . import paths
from .media_probe import ProbeError, probe

# 8 bytes (64 bits) of content hash: enough to tell apart every upload a folder will ever hold.
_HASH_BYTES = 8


class AssetError(ValueError):
    pass


def path_of(name: str) -> Path:
    # Only ever a file directly inside the assets folder.
    return paths.assets_dir() / Path(name).name


def exists(name: str) -> bool:
    return bool(name) and path_of(name).is_file()


def remove_if_unused(name: str, still_used: set[str]) -> None:
    if name and name not in still_used:
        path_of(name).unlink(missing_ok=True)


def is_image(name: str) -> bool:
    """True for a stored still or animated image, False for a video clip."""
    try:
        with Image.open(path_of(name)):
            return True
    except (UnidentifiedImageError, OSError):
        return False


def save_banner(original_name: str, data: bytes) -> str:
    image = _open_image(data)
    if image is None:
        return _save_video(original_name, data)
    frames = getattr(image, "n_frames", 1)
    if frames <= 1:
        still = ImageOps.exif_transpose(image)
        has_alpha = still.mode in ("RGBA", "LA", "PA") or "transparency" in still.info
        out = io.BytesIO()
        still.convert("RGBA" if has_alpha else "RGB").save(out, format="PNG")
        return _store(original_name, out.getvalue(), ".png")
    name = _store(original_name, data, Path(original_name).suffix.lower() or ".img")
    if _ffmpeg_frame_count(path_of(name)) == frames:
        return name
    path_of(name).unlink(missing_ok=True)
    return _store(original_name, _to_apng(image), ".png")


def save_font(original_name: str, data: bytes) -> str:
    try:
        if data[:4] == b"ttcf":  # OpenType collection header
            TTCollection(io.BytesIO(data)).close()
        else:
            TTFont(io.BytesIO(data)).close()
    except (TTLibError, OSError, ValueError, AssertionError) as exc:
        raise AssetError(f"{original_name} is not a font file this app can read ({exc}).") from exc
    return _store(original_name, data, Path(original_name).suffix.lower() or ".ttf")


def save_cookies(original_name: str, data: bytes) -> str:
    name = _store(original_name, data, ".txt")
    os.chmod(path_of(name), 0o600)  # login cookies: readable by this user only
    return name


def _open_image(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _save_video(original_name: str, data: bytes) -> str:
    name = _store(original_name, data, Path(original_name).suffix.lower() or ".mp4")
    try:
        probe(path_of(name))
    except ProbeError as exc:
        path_of(name).unlink(missing_ok=True)
        raise AssetError(f"{original_name} is not an image, GIF or video that ffmpeg can read.") from exc
    return name


def _ffmpeg_frame_count(path: Path) -> int | None:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    text = proc.stdout.strip().split(",")[0]
    return int(text) if proc.returncode == 0 and text.isdigit() else None


def _to_apng(image: Image.Image) -> bytes:
    frames, durations = [], []
    for frame in ImageSequence.Iterator(image):
        frames.append(frame.convert("RGBA"))
        durations.append(frame.info.get("duration"))
    out = io.BytesIO()
    options = {"duration": durations} if all(d for d in durations) else {}
    frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:], loop=0, **options)
    return out.getvalue()


def _store(original_name: str, data: bytes, suffix: str) -> str:
    stem = regex.sub(r"[^\p{L}\p{N}_-]+", "-", Path(original_name).stem).strip("-") or "file"
    name = f"{stem}-{hashlib.blake2s(data, digest_size=_HASH_BYTES).hexdigest()}{suffix}"
    target = path_of(name)
    if not target.exists():
        tmp = target.with_name(f".{target.name}.part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return name
