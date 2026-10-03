"""Copy ONE site's login cookies from a browser on this computer into a
cookies.txt, to upload to a hosted copy of the app (a server has no browser to
read a login from, and Instagram is quicker to ask cloud servers to log in).

Only cookies of the named site and its subdomains are written, never the
browser's whole cookie jar: that would carry every other login (email, bank,
...) to the server. The file is in the Netscape format yt-dlp reads.

Not in here: storing the uploaded file on the server (assets.save_cookies).
Called by: ui/settings_tab.py (local copies only), tests.
"""
from __future__ import annotations

import os
import tempfile
from http.cookiejar import CookieJar

import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser


class ExportError(RuntimeError):
    pass


def belongs(cookie_domain: str, site: str) -> bool:
    """True when a cookie set for `cookie_domain` is sent to `site` or its subdomains' pages."""
    domain = cookie_domain.lstrip(".").lower()
    site = site.strip().lstrip(".").lower()
    return bool(site) and (domain == site or domain.endswith("." + site))


def only_site(jar: CookieJar, site: str) -> YoutubeDLCookieJar:
    kept = YoutubeDLCookieJar()
    for cookie in jar:
        if belongs(cookie.domain, site):
            kept.set_cookie(cookie)
    return kept


def export(browser: str, site: str) -> bytes:
    """cookies.txt bytes with `site`'s cookies from `browser` (a yt-dlp browser name)."""
    try:
        jar = extract_cookies_from_browser(browser)
    except (OSError, ValueError, yt_dlp.utils.YoutubeDLError) as exc:
        raise ExportError(f"Could not read {browser}'s cookies: {exc}") from exc
    kept = only_site(jar, site)
    if not len(kept):
        raise ExportError(f"{browser} has no cookies for {site}. Log in to {site} in {browser} first.")
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        kept.save(path, ignore_discard=True, ignore_expires=False)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)
