"""Facts about where the app runs, read from the live environment.

- hosted(): $REEL_FRAMER_HOSTED is set (the Docker image sets it). The app runs
  on a server: there is no local browser to read an Instagram login from, and
  the page asks for a password (ui/password_gate.py).
- cpu_budget(): how many CPUs this process may really use. A container sees
  every core of its host but may be allowed only a share of them (a cgroup CPU
  quota, e.g. 1 CPU on a 64-core host). ffmpeg sizes its thread pools from the
  visible cores, so under a quota it starts far too many threads, each with its
  own frame buffers. The budget is the cgroup v2 quota (cpu.max), else the v1
  quota (cpu.cfs_quota_us / cpu.cfs_period_us), capped by the CPUs this process
  may run on (sched_getaffinity, else os.cpu_count()). A fractional quota is
  rounded up: one thread can still use half a CPU.
- thread_limit(): the thread count to give ffmpeg, or None when no quota sits
  below the visible cores (ffmpeg's own choice is then right).
- fractional_cpu(): the quota is less than one whole CPU (e.g. Render's free
  plan, 0.1 CPU): even one preview frame takes many seconds, so the settings
  preview waits for a click instead of redrawing on every change.
- host_cookies_file(): a cookies.txt the host provides ($REEL_FRAMER_COOKIES_FILE,
  e.g. a Render secret file), used when no cookies.txt was uploaded in Settings.
  It survives restarts on hosts without a persistent disk.
- packaged(): running from a packaged desktop app (PyInstaller sets sys.frozen):
  there is no pip, so yt-dlp updates come from updater.py.

Not in here: using these facts (compose.py, pipeline.py, ui/*).
Called by: pipeline.py, ui/password_gate.py, ui/settings_tab.py, tests.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

_CGROUP_ROOT = Path("/sys/fs/cgroup")  # where Linux mounts the process's control groups


def hosted() -> bool:
    return bool(os.environ.get("REEL_FRAMER_HOSTED"))


def visible_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # macOS has no sched_getaffinity
        return os.cpu_count() or 1


def cpu_quota(root: Path = _CGROUP_ROOT) -> float | None:
    """CPUs allowed by the cgroup quota, or None when there is no quota."""
    try:  # cgroup v2: "<quota> <period>" or "max <period>"
        quota, period = (root / "cpu.max").read_text().split()[:2]
        return None if quota == "max" else int(quota) / int(period)
    except (OSError, ValueError):
        pass
    try:  # cgroup v1: quota -1 means no limit
        quota = int((root / "cpu" / "cpu.cfs_quota_us").read_text())
        period = int((root / "cpu" / "cpu.cfs_period_us").read_text())
        return quota / period if quota > 0 and period > 0 else None
    except (OSError, ValueError):
        return None


def cpu_budget(root: Path = _CGROUP_ROOT) -> int:
    visible = visible_cpus()
    quota = cpu_quota(root)
    return max(1, min(visible, math.ceil(quota))) if quota else visible


def thread_limit(root: Path = _CGROUP_ROOT) -> int | None:
    budget = cpu_budget(root)
    return budget if budget < visible_cpus() else None


def fractional_cpu(root: Path = _CGROUP_ROOT) -> bool:
    quota = cpu_quota(root)
    return quota is not None and quota < 1


def packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def host_cookies_file() -> Path | None:
    configured = os.environ.get("REEL_FRAMER_COOKIES_FILE", "")
    path = Path(configured) if configured else None
    return path if path and path.is_file() and os.access(path, os.R_OK) else None
