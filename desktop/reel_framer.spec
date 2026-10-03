# PyInstaller recipe for the Reel Framer desktop app: "Reel Framer.app" on macOS,
# "Reel Framer\Reel Framer.exe" (plus its _internal folder) on Windows.
# Run through desktop/build_mac.sh or desktop/build_windows.ps1, which first prepare desktop/build/:
#   ffmpeg/bin/   the self-contained ffmpeg + ffprobe (bundle_ffmpeg.py / fetch_windows_deps.py)
#   fribidi/      FriBiDi under the name Pillow's text shaping loads at run time
#                 (libfribidi.dylib on macOS; fribidi.dll and libfribidi-0.dll on Windows)
#   ReelFramer.icns / ReelFramer.ico   the icon (make_icon.py)
#
# What goes in:
# - desktop/launcher.py, the entry point (window + private server + self-test);
# - every reel_framer module (app.py imports them at run time, so they are listed as hidden
#   imports), with what they import: yt-dlp (its own PyInstaller hook adds the extractors),
#   Pillow, fontTools, regex, certifi (yt-dlp and the updater verify HTTPS with its bundle);
# - Streamlit with its web frontend and package metadata (it reads its own version at start-up);
# - pywebview (its hook brings the WebView2 DLLs on Windows);
# - data: app.py, .streamlit/config.toml, defaults/, and the self-contained ffmpeg.
# On macOS the minimum system version is the one the bundled ffmpeg needs (ffmpeg/minos.txt);
# the version is desktop/VERSION, the one place it is set (the Windows installer reads it too).
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
BUILD = ROOT / "desktop" / "build"
MAC, WINDOWS = sys.platform == "darwin", sys.platform == "win32"
BUILD_NUMBER = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip() or "1"

datas = [
    (str(ROOT / "app.py"), "."),
    (str(ROOT / ".streamlit" / "config.toml"), ".streamlit"),
    (str(ROOT / "defaults"), "defaults"),
    (str(BUILD / "ffmpeg"), "ffmpeg"),
]
datas += collect_data_files("streamlit") + copy_metadata("streamlit")
binaries = [(str(lib), ".") for lib in sorted((BUILD / "fribidi").glob("*")) if lib.is_file()]
hiddenimports = collect_submodules("reel_framer") + collect_submodules("streamlit") + ["certifi"]

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "PyInstaller"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Reel Framer", console=False,
          icon=str(BUILD / "ReelFramer.ico") if WINDOWS else None)
coll = COLLECT(exe, a.binaries, a.datas, name="Reel Framer")

if MAC:
    app = BUNDLE(
        coll,
        name="Reel Framer.app",
        icon=str(BUILD / "ReelFramer.icns"),
        bundle_identifier="ai.metty.reelframer",
        info_plist={
            "CFBundleShortVersionString": (ROOT / "desktop" / "VERSION").read_text().strip(),
            "CFBundleVersion": BUILD_NUMBER,
            "LSMinimumSystemVersion": (BUILD / "ffmpeg" / "minos.txt").read_text().strip(),
            "NSHighResolutionCapable": True,
        },
    )
