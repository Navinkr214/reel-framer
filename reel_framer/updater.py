"""Update yt-dlp inside a packaged desktop app, where pip is not available.

Instagram changes often and yt-dlp follows it; a packaged app would otherwise keep
the yt-dlp it was built with. update():
 1. reads PyPI's JSON for yt-dlp (the newest version and its files);
 2. downloads that version's pure-Python wheel (py3-none-any);
 3. checks the download against the SHA-256 PyPI publishes for it;
 4. unpacks it into site_dir() (<home>/python), replacing an earlier update in one
    rename, and refusing any archive entry that would land outside that folder.
The desktop launcher puts site_dir() first on sys.path before the app starts, so the
new version is used from the next start. HTTPS is verified with certifi's CA bundle,
which ships inside the packaged app (the system's may not be reachable from it).

Not in here: when to offer it (ui/settings_tab.py) or the restart (desktop/launcher.py).
Called by: ui/settings_tab.py, desktop/launcher.py (site_dir), tests.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import certifi

from . import paths

PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
_PURE_PYTHON_WHEEL = "py3-none-any.whl"


class UpdateError(RuntimeError):
    pass


def site_dir() -> Path:
    return paths.home() / "python"


def _fetch(url: str) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=context) as response:
        return response.read()


def latest() -> tuple[str, str, str]:
    """(version, wheel URL, wheel SHA-256) of the newest yt-dlp on PyPI."""
    try:
        meta = json.loads(_fetch(PYPI_JSON))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"Could not reach PyPI: {exc}") from exc
    version = meta["info"]["version"]
    for item in meta.get("urls", []):
        if item.get("packagetype") == "bdist_wheel" and item["filename"].endswith(_PURE_PYTHON_WHEEL):
            return version, item["url"], item["digests"]["sha256"]
    raise UpdateError(f"PyPI lists no pure-Python wheel for yt-dlp {version}.")


def update() -> str:
    """Install the newest yt-dlp into site_dir(); returns its version."""
    version, url, sha256 = latest()
    try:
        wheel = _fetch(url)
    except OSError as exc:
        raise UpdateError(f"Could not download yt-dlp {version}: {exc}") from exc
    if hashlib.sha256(wheel).hexdigest() != sha256:
        raise UpdateError(f"The yt-dlp {version} download does not match PyPI's checksum; nothing was changed.")
    staging = Path(tempfile.mkdtemp(prefix=".python-", dir=paths.home()))
    try:
        with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
            for member in archive.namelist():
                parts = PurePosixPath(member).parts
                if PurePosixPath(member).is_absolute() or ".." in parts:
                    raise UpdateError(f"The yt-dlp {version} wheel has an unsafe entry: {member}")
            archive.extractall(staging)
        target = site_dir()
        retired = target.with_name(f".{target.name}-retired")
        shutil.rmtree(retired, ignore_errors=True)
        if target.exists():
            os.replace(target, retired)
        os.replace(staging, target)
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return version
