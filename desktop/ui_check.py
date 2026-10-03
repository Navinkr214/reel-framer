"""UI check of the desktop app: start its server the way the window does, open the page in a
real browser engine, and do what a person does - type a link and click "Create videos" once.

Two starts: the first with an empty app folder (a fresh install), the second with what the
first one left there (fonts read, settings saved). For each start the report holds:
- health_s: seconds from starting the server until it answers its health check (the window
  shows its "Starting" page for this long);
- page_s: seconds from opening the page until the Create button is on screen and the page
  has finished running;
- one_click: whether a single click on Create, straight after typing a link (no Ctrl+Enter,
  no click elsewhere first), started that link's job; click_s: seconds until the job's
  result showed.
The link is on example.invalid, a name that never resolves (RFC 6761), so the job fails at
once without network traffic; its failure message is enough to show the click started it.
A screenshot of every step and each start's server log are saved next to the report.

Usage:  python desktop/ui_check.py APP REPORT.json [--browser NAME] [--timeout SECONDS]
  APP: the packaged app ("Reel Framer.exe", or "Reel Framer.app/Contents/MacOS/Reel Framer"),
       or desktop/launcher.py for the source tree.
  The server finds ffmpeg on PATH: put the bundled ffmpeg first on PATH, as the window
  process does (desktop.yml does this).
Exit status 0 when every start passed the one-click check. Timings are reported, not judged.
Needs: playwright (desktop/requirements.txt) and the browser --browser names (Edge on
Windows and Chrome elsewhere by default: installed browsers, nothing is downloaded).

Not in here: the self-test of what the app renders (launcher.py --self-test).
Run by: .github/workflows/desktop.yml after each build; by hand for a local build.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launcher  # noqa: E402  (free_port, wait_until_ready, stop_server: the window's own helpers)

LINK = "https://example.invalid/reel.mp4"
BUTTON = "Create videos"
LINKS_BOX = "Reel / video links — one per line"
# Streamlit marks its root element with the script's state; this is the page at rest.
PAGE_IDLE = ('[data-testid="stApp"][data-test-script-state="notRunning"]'
             '[data-test-connection-state="CONNECTED"]')


def start_server(app: Path, port: int, home: Path, log_path: Path) -> subprocess.Popen:
    """The packaged server, started with the flags launcher.start_server uses."""
    command = [sys.executable, str(app)] if app.suffix == ".py" else [str(app)]
    env = dict(os.environ, REEL_FRAMER_HOME=str(home), REEL_FRAMER_OUTPUT_DIR=str(home / "videos"),
               REEL_FRAMER_PARENT_PIPE="stdin")  # it stops by itself if this script dies
    options: dict = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    log = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen([*command, "--serve", str(port)], env=env, stdin=subprocess.PIPE,
                            stdout=log, stderr=subprocess.STDOUT, **options)


def check_start(browser, app: Path, home: Path, shots: Path, name: str, timeout_s: float) -> dict:
    port = launcher.free_port()
    url = f"http://127.0.0.1:{port}"
    result: dict = {"start": name}
    began = time.monotonic()
    server = start_server(app, port, home, shots / f"{name}-server.log")
    try:
        if not launcher.wait_until_ready(server, url):
            result["error"] = f"the server stopped before answering; see {name}-server.log"
            return result
        result["health_s"] = round(time.monotonic() - began, 2)

        page = browser.new_page()
        page.set_default_timeout(timeout_s * 1000)
        opened = time.monotonic()
        page.goto(url)
        button = page.get_by_role("button", name=BUTTON)
        button.wait_for(state="visible")
        page.locator(PAGE_IDLE).wait_for()
        result["page_s"] = round(time.monotonic() - opened, 2)
        page.screenshot(path=shots / f"{name}-1-page.png", full_page=True)

        page.get_by_role("textbox", name=LINKS_BOX).fill(LINK)  # like a paste: not applied yet
        result["button_enabled_after_typing"] = button.is_enabled()
        page.screenshot(path=shots / f"{name}-2-typed.png", full_page=True)
        box = button.bounding_box()
        clicked = time.monotonic()
        # A person's click lands wherever the button is, enabled or not (Playwright's own
        # click() would wait for the button to become enabled, which a person does not).
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        try:
            page.locator('[data-testid="stAlert"]').filter(has_text=LINK).first.wait_for(state="visible")
            result["one_click"] = True
            result["click_s"] = round(time.monotonic() - clicked, 2)
        except PlaywrightTimeout:
            result["one_click"] = False
            result["button_enabled_after_click"] = button.is_enabled()
        page.screenshot(path=shots / f"{name}-3-after-click.png", full_page=True)
        page.close()
        return result
    finally:
        launcher.stop_server(server)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("app", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--browser", default="msedge" if os.name == "nt" else "chrome",
                        help="Playwright browser channel: an installed Edge or Chrome")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="seconds any one step may take before it counts as failed "
                             "(an upper bound for a hang, long enough for a cold start on a slow laptop)")
    args = parser.parse_args()
    shots = args.report.parent
    shots.mkdir(parents=True, exist_ok=True)
    report: dict = {"app": str(args.app), "browser": args.browser, "starts": []}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.browser)
        for name in ("first-start", "second-start"):
            report["starts"].append(check_start(browser, args.app.resolve(), Path(home), shots, name, args.timeout))
        browser.close()
    report["ok"] = all(start.get("one_click") for start in report["starts"])
    text = json.dumps(report, indent=2)
    args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
