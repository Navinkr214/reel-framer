"""Where Reel Framer keeps its files.

Everything lives under one home folder: $REEL_FRAMER_HOME when set, else
`data/` next to app.py. Finished videos go to Settings.output_dir, else
$REEL_FRAMER_OUTPUT_DIR (the desktop app sets Movies/Reel Framer), else
`<home>/output`.

Not in here: what is stored in those folders (settings.py, assets.py).
Called by: settings.py, assets.py, pipeline.py, ui/*.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def home() -> Path:
    return _ensure(Path(os.environ.get("REEL_FRAMER_HOME") or PROJECT_DIR / "data").expanduser())


def settings_file() -> Path:
    return home() / "settings.json"


def assets_dir() -> Path:
    """Uploaded banners, fonts and cookie files."""
    return _ensure(home() / "assets")


def downloads_dir() -> Path:
    """Downloaded and uploaded source videos (reused for previews and re-renders)."""
    return _ensure(home() / "downloads")


def work_dir() -> Path:
    """Per-job scratch folders (caption images); each job removes its own."""
    return _ensure(home() / "work")


def logs_dir() -> Path:
    """ffmpeg error logs of failed jobs."""
    return _ensure(home() / "logs")


def output_dir(configured: str = "") -> Path:
    chosen = configured or os.environ.get("REEL_FRAMER_OUTPUT_DIR", "")
    return _ensure(Path(chosen).expanduser() if chosen else home() / "output")


# Longest file name NTFS, exFAT and FAT32 accept: used only if Windows cannot be asked.
_WINDOWS_NAME_MAX = 255


def max_name_length(folder: Path) -> int:
    """Longest file name the file system holding `folder` accepts (asked of the system)."""
    if hasattr(os, "pathconf"):
        return os.pathconf(folder, "PC_NAME_MAX")
    import ctypes

    root = os.path.splitdrive(str(Path(folder).resolve()))[0] + "\\"
    length = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), None, 0, None,
                                                       ctypes.byref(length), None, None, 0)
    return length.value if ok and length.value else _WINDOWS_NAME_MAX


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
