"""Draw Reel Framer's app icon and write it as a macOS .icns or a Windows .ico.

The picture is the app's idea: a tall reel frame with a banner across its top and
bottom and the video (a play mark) between them. Geometry follows Apple's macOS icon
grid: a 1024 px canvas holding an 824 px rounded square (corner radius about 22.5 %
of it), so the icon sits in line with the system's own. The .icns is built by macOS's
iconutil from the standard iconset sizes; the .ico holds Windows' standard icon sizes.

Usage:  python desktop/make_icon.py <out.icns | out.ico>
Called by: desktop/build_mac.sh, desktop/build_windows.ps1.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

CANVAS, BODY = 1024, 824                 # Apple's macOS icon grid
CORNER = round(BODY * 0.225)
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
TOP, BOTTOM = "#1B1F3B", "#3B2A6B"       # body gradient
BANNER, PLAY, SCREEN = "#7CF0C8", "#FFFFFF", "#FF7A59"


def draw() -> Image.Image:
    icon = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    gradient = Image.new("RGB", (1, BODY))
    top, bottom = Image.new("RGB", (1, 1), TOP).getpixel((0, 0)), Image.new("RGB", (1, 1), BOTTOM).getpixel((0, 0))
    for y in range(BODY):
        t = y / (BODY - 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    body = gradient.resize((BODY, BODY))
    mask = Image.new("L", (BODY, BODY), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, BODY - 1, BODY - 1), radius=CORNER, fill=255)
    offset = (CANVAS - BODY) // 2
    icon.paste(body, (offset, offset), mask)

    draw_ = ImageDraw.Draw(icon)
    frame_h = round(BODY * 0.70)
    frame_w = round(frame_h * 9 / 16)    # a reel is 9:16
    x0, y0 = (CANVAS - frame_w) // 2, (CANVAS - frame_h) // 2
    radius = round(frame_w * 0.12)
    draw_.rounded_rectangle((x0, y0, x0 + frame_w, y0 + frame_h), radius=radius, fill=SCREEN)
    band = round(frame_h * 0.17)
    draw_.rounded_rectangle((x0, y0, x0 + frame_w, y0 + band), radius=radius, fill=BANNER)
    draw_.rectangle((x0, y0 + band - radius, x0 + frame_w, y0 + band), fill=BANNER)
    draw_.rounded_rectangle((x0, y0 + frame_h - band, x0 + frame_w, y0 + frame_h), radius=radius, fill=BANNER)
    draw_.rectangle((x0, y0 + frame_h - band, x0 + frame_w, y0 + frame_h - band + radius), fill=BANNER)
    cx, cy, r = CANVAS // 2, CANVAS // 2, round(frame_w * 0.22)
    draw_.polygon([(cx - r * 0.6, cy - r), (cx - r * 0.6, cy + r), (cx + r, cy)], fill=PLAY)
    return icon


def main(out: Path) -> None:
    icon = draw()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".ico":
        icon.save(out, sizes=ICO_SIZES)
        print(f"wrote {out}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "ReelFramer.iconset"
        iconset.mkdir()
        for size, scale in ICONSET:
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            icon.resize((size * scale, size * scale), Image.Resampling.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
        icon.save(out.with_suffix(".png"))
    print(f"wrote {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve())
