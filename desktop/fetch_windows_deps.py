"""Gather what the Windows app bundles besides Python packages:

    <out>/ffmpeg/bin/ffmpeg.exe, ffprobe.exe   a static GPL build of ffmpeg
    <out>/fribidi/*.dll                       FriBiDi, which Pillow's text shaping loads at run time

ffmpeg comes from BtbN/FFmpeg-Builds (the Windows builds ffmpeg.org links to): the newest
release's "nX.Y-latest-win64-gpl" zip with the highest X.Y, checked against the SHA-256
that release publishes in checksums.sha256 before anything is unpacked.

FriBiDi comes from an MSYS2 installation (GitHub's Windows runners have one at C:\\msys64;
install it with `pacman -S mingw-w64-ucrt-x86_64-fribidi`). The DLL is copied under both
names Pillow tries on Windows, "fribidi.dll" and MSYS2's "libfribidi-0.dll".

Usage:  python desktop/fetch_windows_deps.py <out-dir> [<msys2-root>]
Called by: desktop/build_windows.ps1.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

import certifi

RELEASES = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
ASSET = re.compile(r"^ffmpeg-n(\d+)\.(\d+)-latest-win64-gpl-\d+\.\d+\.zip$")
PROGRAMS = ("ffmpeg.exe", "ffprobe.exe")
FRIBIDI_NAMES = ("fribidi.dll", "libfribidi-0.dll")


def fetch(url: str) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(url, headers={"User-Agent": "reel-framer-build"})
    with urllib.request.urlopen(request, context=context) as response:
        return response.read()


def ffmpeg(out: Path) -> None:
    release = json.loads(fetch(RELEASES))
    assets = {a["name"]: a["browser_download_url"] for a in release["assets"]}
    builds = [(tuple(map(int, m.groups())), name) for name in assets if (m := ASSET.match(name))]
    if not builds:
        sys.exit("No win64 GPL release build found in BtbN/FFmpeg-Builds' latest release.")
    _, name = max(builds)
    sums = {}  # sha256sum format: "<hash>  <name>" ("*<name>" when written in binary mode)
    for line in fetch(assets["checksums.sha256"]).decode().splitlines():
        if line.strip():
            digest, file_name = line.split(maxsplit=1)
            sums[file_name.strip().lstrip("*")] = digest
    archive = fetch(assets[name])
    if hashlib.sha256(archive).hexdigest() != sums.get(name, "").strip():
        sys.exit(f"{name} does not match its published SHA-256; nothing unpacked.")
    target = out / "ffmpeg" / "bin"
    shutil.rmtree(out / "ffmpeg", ignore_errors=True)
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for member in zipped.namelist():
            if Path(member).name in PROGRAMS and Path(member).parent.name == "bin":
                (target / Path(member).name).write_bytes(zipped.read(member))
    missing = [p for p in PROGRAMS if not (target / p).exists()]
    if missing:
        sys.exit(f"{name} has no {', '.join(missing)}.")
    print(f"ffmpeg: {name}")


def fribidi(out: Path, msys2: Path) -> None:
    found = sorted((msys2 / "ucrt64" / "bin").glob("libfribidi-*.dll"))
    if not found:
        sys.exit(f"No FriBiDi DLL under {msys2}: run pacman -S mingw-w64-ucrt-x86_64-fribidi.")
    target = out / "fribidi"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name in FRIBIDI_NAMES:
        shutil.copy2(found[-1], target / name)
    print(f"fribidi: {found[-1]}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    out_dir = Path(sys.argv[1]).resolve()
    ffmpeg(out_dir)
    fribidi(out_dir, Path(sys.argv[2] if len(sys.argv) == 3 else r"C:\msys64"))
