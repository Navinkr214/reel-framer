"""cookies_export.py: only the chosen site's cookies leave the browser."""
from __future__ import annotations

import http.cookiejar

import pytest
from yt_dlp.cookies import YoutubeDLCookieJar

from reel_framer import cookies_export


def _cookie(domain: str, name: str) -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0, name=name, value=f"{name}-value", port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path="/", path_specified=True, secure=True, expires=4102444800, discard=False,
        comment=None, comment_url=None, rest={},
    )


def _browser_jar() -> YoutubeDLCookieJar:
    jar = YoutubeDLCookieJar()
    for domain, name in [(".instagram.com", "sessionid"), ("www.instagram.com", "csrftoken"),
                         ("i.instagram.com", "ig_did"), (".notinstagram.com", "trap"),
                         (".google.com", "SID"), ("mybank.example", "bank")]:
        jar.set_cookie(_cookie(domain, name))
    return jar


@pytest.mark.parametrize("domain, site, expected", [
    (".instagram.com", "instagram.com", True),
    ("www.instagram.com", "instagram.com", True),
    ("instagram.com", "Instagram.com", True),
    (".notinstagram.com", "instagram.com", False),
    ("instagram.com.evil.example", "instagram.com", False),
    (".google.com", "instagram.com", False),
    (".instagram.com", "", False),
])
def test_belongs(domain, site, expected):
    assert cookies_export.belongs(domain, site) is expected


def test_export_writes_only_the_site_and_yt_dlp_can_read_it(monkeypatch, tmp_path):
    monkeypatch.setattr(cookies_export, "extract_cookies_from_browser", lambda browser: _browser_jar())
    data = cookies_export.export("chrome", "instagram.com")
    path = tmp_path / "cookies.txt"
    path.write_bytes(data)
    jar = YoutubeDLCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    assert sorted(c.name for c in jar) == ["csrftoken", "ig_did", "sessionid"]
    assert b"google" not in data and b"bank" not in data and b"notinstagram" not in data


def test_export_with_no_login_for_the_site_explains(monkeypatch):
    monkeypatch.setattr(cookies_export, "extract_cookies_from_browser", lambda browser: _browser_jar())
    with pytest.raises(cookies_export.ExportError, match="no cookies for facebook.com"):
        cookies_export.export("chrome", "facebook.com")


def test_unreadable_browser_explains(monkeypatch):
    def missing(browser):
        raise FileNotFoundError("could not find firefox cookies database")
    monkeypatch.setattr(cookies_export, "extract_cookies_from_browser", missing)
    with pytest.raises(cookies_export.ExportError, match="Could not read firefox"):
        cookies_export.export("firefox", "instagram.com")
