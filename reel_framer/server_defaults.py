"""The settings a hosted copy starts from: the repository's defaults/ folder.

A server without a persistent disk (Render's free plan) forgets its settings and
uploads whenever it sleeps, restarts or redeploys. defaults/ (settings.json plus
the banner and font files it names) ships inside the image. A hosted copy whose
home folder has no settings yet copies it in (seed_if_fresh), so every wake-up
starts with the same banners and captions. Local copies never read it.

A local copy writes it (save): the current settings and the files they name, so
the next push carries them to the server. Written without login cookies (they
belong in the host's secret file, never in a repository) and without the local
output folder (a server path differs).

Not in here: the settings model (settings.py) or stored uploads (assets.py).
Called by: app.py (seed), ui/settings_tab.py (save), tests.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

from . import assets, hosting, paths
from . import settings as store
from .settings import Settings

DEFAULTS_DIR = paths.PROJECT_DIR / "defaults"
_ASSET_FONT = "asset:"


def defaults_dir() -> Path:
    return Path(os.environ.get("REEL_FRAMER_DEFAULTS") or DEFAULTS_DIR)


def seed_if_fresh() -> bool:
    """Hosted and no settings yet: start from defaults/. True when it seeded."""
    source = defaults_dir()
    if not hosting.hosted() or paths.settings_file().exists() or not (source / "settings.json").is_file():
        return False
    for file in sorted((source / "assets").glob("*")) if (source / "assets").is_dir() else []:
        target = assets.path_of(file.name)
        if file.is_file() and not target.exists():
            tmp = target.with_name(f".{target.name}.part")
            shutil.copyfile(file, tmp)
            os.replace(tmp, target)
    store.save(store.load(source / "settings.json"))  # through the loader: only known, valid fields
    return True


def referenced_assets(settings: Settings) -> set[str]:
    names = {settings.top_banner, settings.bottom_banner}
    names |= {style.font.removeprefix(_ASSET_FONT) for style in (settings.top_text, settings.bottom_text)
              if style.font.startswith(_ASSET_FONT)}
    return {name for name in names if name and assets.exists(name)}


def save(settings: Settings) -> Path:
    """Write defaults/ from these settings; returns the folder."""
    target = defaults_dir()
    asset_dir = target / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    keep = referenced_assets(settings)
    for name in keep:
        shutil.copyfile(assets.path_of(name), asset_dir / name)
    for stale in asset_dir.iterdir():
        if stale.is_file() and stale.name not in keep:
            stale.unlink()
    clean = replace(settings, cookies_browser="", cookies_file="", output_dir="")
    store.save(clean, target / "settings.json")
    return target
