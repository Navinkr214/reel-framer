"""Reel Framer desktop app: a window on a private, local Reel Framer server.

Opening the app:
  1. asks the operating system for a free port on 127.0.0.1 (nothing outside this
     computer can reach it);
  2. starts a second copy of itself with `--serve PORT`, which runs the Streamlit app
     in its own process group, with the bundled ffmpeg first on PATH; before it answers,
     that copy imports the app, reads the installed fonts and tests the video encoders
     (warm_up), so the page opens ready instead of filling in while it is used;
  3. opens the window straight away on a "Starting" page that counts the seconds, and
     loads the app as soon as the server answers its health check - or shows the
     server's log if it stops first;
  4. when the window closes, stops the server and everything it started (ffmpeg too).
     If this process ends any other way (Quit, Force Quit, a crash), the server notices
     on its own: it holds the read end of a pipe whose only writer is this process, and
     when that pipe closes it stops itself and everything it started.

Folders: settings, uploads and downloads go to the user's application-data folder
(macOS: ~/Library/Application Support/Reel Framer); finished videos to
~/Movies/Reel Framer (Videos/Reel Framer elsewhere). A yt-dlp installed later by
Settings → Maintenance (reel_framer/updater.py) sits in <data>/python and is put
first on sys.path before the app starts.

`--self-test report.json` exercises the app without its window (bundled ffmpeg,
text shaping, per-script caption fonts, colour emoji, a render with sound through every
usable encoder, HTTPS) and writes a JSON report; the exit code is 0 when all passed.
CI runs it on the packaged app, and it is handy on a user's machine.

On Windows, console programs started by this windowed app (ffmpeg, ffprobe) get no
console window of their own, and if no Edge WebView2 window can be made the app opens in
the default browser with a "click OK to stop" message instead.

Run from the source tree too:  .venv/bin/python desktop/launcher.py
Built into the app by desktop/build_mac.sh / desktop/build_windows.ps1 (PyInstaller).
"""
from __future__ import annotations

import ast
import html
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Reel Framer"
PACKAGED = bool(getattr(sys, "frozen", False))
# The packaged app unpacks its files under sys._MEIPASS; from source, the project folder.
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
# How often to ask a starting server whether it is ready: sooner than anyone notices.
READY_POLL_SECONDS = 0.2
# Streamlit stops in well under a second; this only bounds a server that hangs on the way out.
STOP_GRACE_SECONDS = 5
# Share of the screen the window opens at.
WINDOW_SHARE = 0.85


def data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "reel-framer"


def videos_dir() -> Path:
    return Path.home() / ("Movies" if sys.platform == "darwin" else "Videos") / APP_NAME


def server_env() -> dict[str, str]:
    env = dict(os.environ)
    env["REEL_FRAMER_PARENT_PIPE"] = "stdin"   # serve(): stop when the window process is gone
    env.setdefault("REEL_FRAMER_HOME", str(data_dir()))
    env.setdefault("REEL_FRAMER_OUTPUT_DIR", str(videos_dir()))
    bundled_ffmpeg = RESOURCES / "ffmpeg" / "bin"
    if bundled_ffmpeg.is_dir():
        env["PATH"] = os.pathsep.join([str(bundled_ffmpeg), env.get("PATH", "")])
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ---- Server process ---------------------------------------------------------------------------

def prefer_updated_packages() -> None:
    """Put a yt-dlp installed by the updater ahead of the bundled one (see reel_framer/updater.py)."""
    updated = Path(os.environ.get("REEL_FRAMER_HOME") or data_dir()) / "python"
    if updated.is_dir():
        sys.path.insert(0, str(updated))


def stop_with_parent() -> None:
    """Stop this server (and what it started) once the window process's pipe closes."""
    def watch() -> None:
        # Raw reads of the descriptor, not sys.stdin.buffer.read(): a thread waiting inside
        # stdin's buffered reader holds its lock, and when the server then stopped normally
        # Python aborted at exit ("could not acquire lock for <stdin> at interpreter
        # shutdown"), a crash report on every quit of the app.
        while os.read(sys.stdin.fileno(), io.DEFAULT_BUFFER_SIZE):
            pass  # the window process never writes: this ends when its end of the pipe is gone
        if os.name == "posix":
            os.killpg(os.getpgrp(), signal.SIGTERM)
        else:  # this process and everything it started (ffmpeg renders)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(os.getpid())], capture_output=True)
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def hide_console_windows() -> None:
    """Windows: start console programs (ffmpeg, ffprobe, from any module) without a window each."""
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


def warm_up() -> str:
    """The app's slow first-use work, done before the server answers so that the window keeps
    its "Starting" page up instead of showing a page that is still filling in: import what
    app.py imports, read the installed fonts (on Windows kept on disk after the first start)
    and test which video encoders work on this computer. Returns a timing line for the log."""
    sys.path.insert(0, str(RESOURCES))
    parts: list[str] = []

    def timed(label: str, work) -> None:
        began = time.monotonic()
        detail = work()
        parts.append(f"{label} {time.monotonic() - began:.1f} s" + (f" ({detail})" if detail else ""))

    def import_app_modules() -> None:  # app.py's own import statements, so the list never drifts
        tree = ast.parse((RESOURCES / "app.py").read_text(encoding="utf-8"))
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        exec(compile(ast.Module(body=imports, type_ignores=[]), "app.py", "exec"), {})

    timed("imports", import_app_modules)
    from reel_framer import encoder
    from reel_framer.text import backend
    timed("fonts", lambda: f"{len(backend.list_faces())} faces")
    timed("encoders", lambda: ", ".join(encoder.video_encoders()))
    return "warm-up: " + ", ".join(parts)


def serve(port: int) -> None:
    """Run the Streamlit app on 127.0.0.1:port in this process (blocks until stopped)."""
    if os.environ.get("REEL_FRAMER_PARENT_PIPE") == "stdin":
        stop_with_parent()
    if os.name == "nt":
        hide_console_windows()
    prefer_updated_packages()
    os.chdir(RESOURCES)  # app.py imports reel_framer from here; Streamlit reads .streamlit/ here
    from streamlit.web import bootstrap

    flags = {
        "server.port": port,
        "server.address": "127.0.0.1",
        "server.headless": True,
        "server.fileWatcherType": "none",   # the app's files never change inside the app
        "browser.gatherUsageStats": False,
        "global.developmentMode": False,    # serve the bundled frontend, not a dev server
        "client.toolbarMode": "minimal",    # no "Deploy" button in a desktop window
    }
    bootstrap.load_config_options(flag_options=flags)
    print(warm_up(), flush=True)  # before the server answers its health check
    bootstrap.run(str(RESOURCES / "app.py"), False, [], flags)


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    me = [sys.executable] if PACKAGED else [sys.executable, str(Path(__file__).resolve())]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w", encoding="utf-8")
    options: dict = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return subprocess.Popen([*me, "--serve", str(port)], env=server_env(), stdin=subprocess.PIPE,
                            stdout=log, stderr=subprocess.STDOUT, **options)


def stop_server(server: subprocess.Popen) -> None:
    """Stop the server and every process it started (ffmpeg renders included)."""
    if server.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(server.pid, signal.SIGTERM)
    else:
        subprocess.run(["taskkill", "/T", "/PID", str(server.pid)], capture_output=True)
    try:
        server.wait(STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(server.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(server.pid)], capture_output=True)
        server.wait()


def wait_until_ready(server: subprocess.Popen, url: str) -> bool:
    """True once the server answers its health check; False if it exits first."""
    while server.poll() is None:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=READY_POLL_SECONDS * 5) as reply:
                if reply.status == 200:
                    return True
        except OSError:
            time.sleep(READY_POLL_SECONDS)
    return False


# ---- Self-test ------------------------------------------------------------------------------

SELF_TEST_TEXT = "Self-test नमस्ते 🔥"   # Latin, an Indic script and an emoji: three kinds of fallback


def self_test(report_path: Path) -> int:
    """Exercise the app without its window; write a JSON report; 0 when every check passed."""
    report: dict = {"packaged": PACKAGED, "platform": sys.platform, "checks": {}}
    checks = report["checks"]
    with tempfile.TemporaryDirectory() as home:
        os.environ.update(server_env())
        os.environ.update({"REEL_FRAMER_HOME": home, "REEL_FRAMER_OUTPUT_DIR": str(Path(home) / "out")})
        os.environ.pop("REEL_FRAMER_PARENT_PIPE", None)
        if os.name == "nt":
            hide_console_windows()
        prefer_updated_packages()
        sys.path.insert(0, str(RESOURCES))
        from PIL import Image, features

        from reel_framer import assets, encoder, pipeline, updater
        from reel_framer.media_probe import probe
        from reel_framer.settings import Settings
        from reel_framer.text import backend
        from reel_framer.text.faces import covers, has_color_glyphs

        ffmpeg = shutil.which("ffmpeg") or ""
        report["ffmpeg"] = ffmpeg
        checks["ffmpeg is the bundled one"] = (not PACKAGED) or Path(ffmpeg).resolve().is_relative_to(RESOURCES.resolve())
        checks["text shaping (raqm)"] = bool(features.check_feature("raqm"))
        report["font_backend"] = backend.name()
        spans = backend.segment(SELF_TEST_TEXT, backend.base_from_setting("", True), 64)
        report["caption_faces"] = [[SELF_TEST_TEXT[a:b], Path(f.path).name] for a, b, f in spans]
        checks["caption faces have their characters"] = all(
            covers(f, SELF_TEST_TEXT[a:b].strip()[:1]) for a, b, f in spans if SELF_TEST_TEXT[a:b].strip())
        emoji_faces = [f for a, b, f in spans if "🔥" in SELF_TEST_TEXT[a:b]]
        checks["emoji in colour"] = bool(emoji_faces) and has_color_glyphs(emoji_faces[0])
        report["encoders"] = list(encoder.video_encoders())
        sample, banner = Path(home) / "sample.mp4", Path(home) / "banner.png"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", "-shortest",
                        "-pix_fmt", "yuv420p", str(sample)], check=True, capture_output=True)
        Image.new("RGB", (360, 90), (14, 124, 102)).save(banner)
        renders = {}
        for name in report["encoders"]:
            settings = Settings(frame_aspect="9:16", video_encoder=name)
            settings.top_banner = assets.save_banner("banner.png", banner.read_bytes())
            settings.top_text.text = SELF_TEST_TEXT
            try:
                info = probe(pipeline.process_file(sample, settings).output)
                renders[name] = {"codec": info.video_codec, "audio": info.audio_codec,
                                 "size": [info.width, info.height], "duration": info.duration}
            except Exception as exc:  # report every failure, keep testing the other encoders
                renders[name] = {"error": f"{type(exc).__name__}: {exc}"}
        report["renders"] = renders
        checks["every encoder rendered video with sound"] = bool(renders) and all(
            r.get("codec") == encoder.OUTPUT_VIDEO_CODEC and r.get("audio") for r in renders.values())
        try:
            report["https"] = f"ok (newest yt-dlp on PyPI: {updater.latest()[0]})"
        except updater.UpdateError as exc:  # offline is not a broken app; CI checks this field itself
            report["https"] = f"failed: {exc}"
    report["ok"] = all(checks.values())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


# ---- Window ---------------------------------------------------------------------------------

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
font:15px -apple-system,'Segoe UI',sans-serif;background:#0E1117;color:#FAFAFA}}
main{{max-width:760px;padding:24px}} pre{{white-space:pre-wrap;font-size:12px;opacity:.8}}
</style></head><body><main>{body}</main></body></html>"""
# Shown until the server answers. The seconds count up, so a slow start (the warm-up) never
# looks frozen.
_STARTING = ('<p>Starting Reel Framer… <span id="seconds"></span></p>'
             '<p style="opacity:.7">The first start takes the longest.</p>'
             '<script>var s = 0; setInterval(function () {'
             ' document.getElementById("seconds").textContent = ++s + " s"; }, 1000);</script>')


def main() -> None:
    import webview

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    log_path = data_dir() / "server.log"
    server = start_server(port, log_path)
    screen = webview.screens[0] if webview.screens else None
    size = {"width": int(screen.width * WINDOW_SHARE), "height": int(screen.height * WINDOW_SHARE)} if screen else {}
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(APP_NAME, html=_PAGE.format(body=_STARTING),
                                   text_select=True, **size)

    def load_when_ready() -> None:
        if wait_until_ready(server, url):
            window.load_url(url)
        else:
            log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            window.load_html(_PAGE.format(body="<h3>Reel Framer could not start</h3>"
                                               f"<p>Server log ({html.escape(str(log_path))}):</p>"
                                               f"<pre>{html.escape(log)}</pre>"))

    try:
        # On Windows insist on Edge WebView2: the older engine pywebview falls back to cannot run the app.
        webview.start(load_when_ready, gui="edgechromium" if os.name == "nt" else None,
                      private_mode=False, storage_path=str(data_dir() / "webview"))
    except Exception:  # no usable web view on this machine: use the browser instead
        if wait_until_ready(server, url):
            webbrowser.open(url)
            _tell("Reel Framer is running in your web browser.\n\nClick OK to stop it.")
    finally:
        stop_server(server)


def _tell(message: str) -> None:
    """A blocking message box (the app's only window when the web view is unavailable)."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0)
    elif sys.platform == "darwin":
        script = f'display dialog {json.dumps(message)} with title {json.dumps(APP_NAME)} buttons {{"OK"}}'
        subprocess.run(["osascript", "-e", script], check=False)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(Path(sys.argv[2])))
    else:
        main()
