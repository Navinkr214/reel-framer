# Reel Framer

Paste an Instagram reel (or any video) link → get the video back **shrunk into a frame with
your top banner and bottom banner** (image, GIF or short clip, looped for the whole video)
and optional captions in any language → watch it and download the MP4.

```
┌──────────────────────┐
│      TOP BANNER      │  image / GIF / clip (+ optional caption)
├──────────────────────┤
│ caption strip (opt.) │
│  ┌────────────────┐  │
│  │                │  │
│  │   the reel,    │  │  blurred copy of the video
│  │   shrunk to    │  │  (or a solid colour) fills the sides
│  │   fit          │  │
│  └────────────────┘  │
├──────────────────────┤
│    BOTTOM BANNER     │  e.g. an animated "Follow @yourpage" GIF
└──────────────────────┘
```

## Start

Needs ffmpeg (`brew install ffmpeg`) and Python 3.10+.

```bash
cd reel_framer
./run.sh
```

The first run creates `.venv` and installs the requirements; then the app opens at
http://localhost:8501 (reachable only from this computer; see `.streamlit/config.toml`).

## Use

1. **Settings** tab → upload the **top** and **bottom** banners (PNG/JPG/WebP, GIF, or a short
   MP4/MOV clip). Settings are saved as you change them; the **Preview** on the right shows a
   frame from your most recent video.
2. Optional **captions**: any language and emoji. Place them on the banner, in their own strip
   next to the video, or on the video. You can pick the font, colours, outline and box.
   The default system font picks the right font for each script (on macOS it uses the OS's own fallback).
3. **Frame shape**: same as the video, or 9:16 / 4:5 / 1:1 / 16:9 / custom.
4. **Create** tab → paste one or more links (one per line) and/or upload video files →
   **Create videos** → watch and **Download MP4**. Every video is also saved in
   `data/output/` (or the folder set in Settings → Output).

## Instagram login

Public reels usually download without logging in. If Instagram asks for a login (a private
account, or a rate limit after many downloads), open Settings → **Instagram login** and either
pick the browser where you are logged in to Instagram or upload a `cookies.txt` exported from it.
With Chrome, macOS asks once to allow access to "Chrome Safe Storage". Safari needs Full Disk
Access for the app that runs `run.sh`. If links stop downloading, use Settings → Maintenance →
**Update yt-dlp** and restart the app (Instagram changes often).

Repost only videos you have the right to use.

## Put it online (Render)

Render runs the app as a normal long-running server. Vercel can't: it runs short serverless
functions, and Streamlit needs a server that stays up and holds a WebSocket per visitor. The
renders also need ffmpeg and minutes of CPU.

1. The code lives in a private GitHub repository.
2. In [Render](https://dashboard.render.com): **New → Blueprint**, connect GitHub, pick the
   `reel-framer` repository, **Apply**. Render reads `render.yaml`: it builds the `Dockerfile`
   (ffmpeg, fonts for every script) as a **free** web service in Singapore and generates a
   random `APP_PASSWORD`.
3. Open the service → **Environment** → copy `APP_PASSWORD`, then open the service's
   `https://….onrender.com` address and enter it. The browser can remember it.

**What the free plan means** (0.1 CPU, 512 MB, no disk):
- It sleeps after 15 minutes without visitors. The first visit after that waits about a
  minute while it wakes.
- Each sleep or deploy wipes what was uploaded or made on it: settings, banners, finished
  videos. Download each video when it's ready.
- It always starts from the repository's `defaults/` folder. Set your banners and captions in
  Reel Framer **on your Mac**, then Settings → Maintenance → **Save as the server's starting
  settings**, and push:
  `git add defaults && git commit -m "Server settings" && git push`. Render redeploys with
  them. The shipped `defaults/` only sets the encoding speed to `veryfast`.
- Rendering is slow. A 32 s 1080×1920 reel needs about 45 s of one Mac core at `veryfast`,
  so with a tenth of a server CPU expect around 8 minutes or more. Keep the page open while
  it renders. If the page reloads, the video still appears under **All finished videos**.
  Setting Frame → Short side to `720` makes renders about twice as fast.
- **Instagram login:** cloud servers are asked to log in far more often than home connections.
  In Reel Framer **on your Mac**, open Settings → Instagram login → **Export a login for a
  server copy**, then **Read the login** and **Download**. In Render, add the file under
  Environment → **Secret Files** as `cookies.txt`; it survives sleeps. The file holds only
  Instagram's cookies, never your other logins. Anyone with it is logged in as you, so keep
  it private; a secondary Instagram account is safer.

**Paid sizes** keep everything on a disk and render faster: change `plan:` in `render.yaml`
and add the `disk:` block shown there. Render's 2026 list prices, check render.com/pricing:

| Plan | Price | Notes |
|---|---|---|
| Free (0.1 CPU / 512 MB) | $0 | No disk; slow; sleeps |
| 0.5 CPU / 512 MB ("Starter") | $7 a month | Disk possible; memory is tight |
| 1 CPU / 2 GB (`1c-2g`, "Standard") | $25 a month + $0.25/GB disk | Comfortable |
| 2 CPU / 4 GB (`2c-4g`, "Pro") | $85 a month | About twice as fast again |

Every push redeploys automatically. yt-dlp is installed when the image is built; Render's
**Manual Deploy → Clear build cache & deploy** picks up the newest one. GitHub Actions
(`.github/workflows/ci.yml`) builds the same image on Linux, runs all tests inside it and
starts the server once, so a broken image shows up there first.

Hosted copies behave differently in a few ways (the image sets `REEL_FRAMER_HOSTED=1`):
- the page asks for `APP_PASSWORD`, and refuses to start without one;
- there is no browser login option, only `cookies.txt`, either uploaded or as a secret file;
- ffmpeg gets one thread per CPU the container may use, read from its CPU quota rather than
  the host's core count;
- videos render one at a time;
- the preview waits for a click when the server has less than one CPU;
- the app runs as an unprivileged user.

To run the same image elsewhere:
`docker build -t reel-framer . && docker run -p 8501:8501 -e APP_PASSWORD=… -v reel-data:/var/data reel-framer`.

## How sizes are decided

Nothing is a fixed pixel size. Each value comes from the video, the banner, or a setting:

| What | Derived from |
|---|---|
| Frame size | the video's own shape and short side (unless you choose a shape / size) |
| Banner | full frame width at the banner's own shape; shrunk if taller than *Banner max height* |
| Video | fitted into the room the banners leave × *Video size*, centred |
| Caption size / outline | % of the frame width / % of the text size |
| Caption box padding | the font's own descent |
| Blur | % of the frame width |
| Bitrate | the source's bits per pixel (never below the source), or constant quality (CRF) |
| Audio | copied if already AAC; otherwise AAC at the source's bitrate |
| Output format | MP4, H.264 (ffmpeg's own H.264 encoder) + AAC, the format Instagram, Shorts and WhatsApp accept |

## Command line

Uses the settings saved by the app:

```bash
.venv/bin/python cli.py "https://www.instagram.com/reel/XXXX/" more-links-or-files.mp4
```

## Files

- `data/settings.json`: your settings
- `data/assets/`: uploaded banners, fonts, cookies.txt (kept readable by your user only)
- `data/downloads/`: downloaded/uploaded source videos, kept for previews and re-renders (Settings → Maintenance deletes them)
- `data/output/`: finished videos
- `defaults/`: the settings (and the banner/font files they name) a hosted copy starts from;
  written by Settings → Maintenance → Save as the server's starting settings
- Set `REEL_FRAMER_HOME=/some/folder` to keep all of this elsewhere.

## Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests use synthetic videos, GIFs and fonts (no network). The downloader is tested against a
local HTTP server, and the password gate is driven through Streamlit's own app tester. On GitHub,
CI runs them inside the Linux image as well.

## Code map

- `app.py`: Streamlit entry point (two tabs)
- `cli.py`: command line
- `reel_framer/`: `paths`, `settings`, `assets`, `media_probe`, `downloader`, `layout`,
  `encoder`, `compose`, `pipeline`, `hosting` (server facts: hosted flag, CPU quota, host
  login file), `server_defaults` (defaults/ for hosted copies), `cookies_export`; `text/`
  (caption fonts, fallback, rendering); `ui/` (tabs, password gate).
  Each file starts with a note on what it holds and who calls it.
- `Dockerfile`, `docker-entrypoint.sh`, `render.yaml`, `.github/workflows/ci.yml`: hosting.

## Known limits

- A line mixing right-to-left text (Arabic, Hebrew) with other scripts shapes each part correctly, but lays the parts out left to right.
- Uploads are limited to 200 MB per file (Streamlit's default; `server.maxUploadSize` in `.streamlit/config.toml` changes it).
- Rendering needs CPU: a 32 s 1080×1920 reel took 16 s on this Mac using all cores at `veryfast`
  (30 s at the default speed), or 32 s / 82 s on a single thread.
