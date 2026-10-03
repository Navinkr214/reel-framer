"""hosting.py (CPU quota, hosted flag) and the password gate on the real app (AppTest)."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from reel_framer import hosting
from reel_framer.ui.password_gate import password_matches

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _cgroup_v2(tmp_path, text):
    (tmp_path / "cpu.max").write_text(text)
    return tmp_path


def _cgroup_v1(tmp_path, quota, period):
    (tmp_path / "cpu").mkdir()
    (tmp_path / "cpu" / "cpu.cfs_quota_us").write_text(str(quota))
    (tmp_path / "cpu" / "cpu.cfs_period_us").write_text(str(period))
    return tmp_path


def test_no_cgroup_means_no_quota(tmp_path):
    assert hosting.cpu_quota(tmp_path) is None
    assert hosting.cpu_budget(tmp_path) == hosting.visible_cpus()
    assert hosting.thread_limit(tmp_path) is None


def test_cgroup_v2_quota(tmp_path):
    root = _cgroup_v2(tmp_path, "100000 100000\n")
    assert hosting.cpu_quota(root) == 1.0
    assert hosting.cpu_budget(root) == 1


def test_cgroup_v2_unlimited(tmp_path):
    assert hosting.cpu_quota(_cgroup_v2(tmp_path, "max 100000\n")) is None


def test_fractional_quota_rounds_up(tmp_path):
    root = _cgroup_v1(tmp_path, 50000, 100000)  # half a CPU
    assert hosting.cpu_quota(root) == 0.5
    assert hosting.cpu_budget(root) == 1


def test_quota_never_exceeds_visible_cpus(tmp_path):
    root = _cgroup_v2(tmp_path, f"{(hosting.visible_cpus() + 3) * 100000} 100000")
    assert hosting.cpu_budget(root) == hosting.visible_cpus()
    assert hosting.thread_limit(root) is None


def test_thread_limit_applies_only_below_visible_cpus(tmp_path):
    if hosting.visible_cpus() < 2:
        pytest.skip("needs a machine that shows at least two CPUs")
    root = _cgroup_v2(tmp_path, "100000 100000")
    assert hosting.thread_limit(root) == 1


def test_hosted_flag(monkeypatch):
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    assert not hosting.hosted()
    monkeypatch.setenv("REEL_FRAMER_HOSTED", "1")
    assert hosting.hosted()


def test_password_comparison():
    assert password_matches("s3cret", "s3cret")
    assert not password_matches("s3cre", "s3cret")
    assert not password_matches("", "s3cret")
    assert password_matches("नमस्ते", "नमस्ते")


def _app():
    return AppTest.from_file(APP, default_timeout=60)


def test_gate_hides_the_app_until_the_password_is_right(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    at = _app().run()
    assert len(at.tabs) == 0 and len(at.text_input) == 1
    at.text_input[0].input("wrong")
    at.button[0].click().run()
    assert len(at.tabs) == 0 and [e.value for e in at.error] == ["Wrong password."]
    at.text_input[0].input("s3cret")
    at.button[0].click().run()
    assert len(at.tabs) == 2 and not at.exception


def test_hosted_without_a_password_stays_closed(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("REEL_FRAMER_HOSTED", "1")
    at = _app().run()
    assert len(at.tabs) == 0 and len(at.text_input) == 0
    assert any("APP_PASSWORD" in e.value for e in at.error)


def test_local_copy_needs_no_password(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REEL_FRAMER_HOSTED", raising=False)
    at = _app().run()
    assert len(at.tabs) == 2 and not at.exception


def test_hosted_settings_offer_no_browser_login(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("REEL_FRAMER_HOSTED", "1")
    at = _app().run()
    at.text_input[0].input("s3cret")
    at.button[0].click().run()
    labels = [box.label for box in at.selectbox]
    assert "Use the login saved in this browser" not in labels
    assert not any(e.label.startswith("Export a login") for e in at.expander)
