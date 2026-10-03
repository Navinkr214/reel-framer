"""Reel Framer: put a top and a bottom banner (image, GIF or short clip) and
optional captions around a downloaded reel or video, and save it as an MP4.

Modules (one concern each):
- paths.py        where settings, uploaded assets, downloads and outputs live
- settings.py     the saved Settings (dataclasses, JSON load/save)
- assets.py       uploaded banners / fonts / cookie files, normalised for ffmpeg
- media_probe.py  ffprobe wrapper -> MediaInfo (display size, fps, codecs, frames)
- downloader.py   yt-dlp download of a reel / post / video link
- layout.py       pure geometry: frame size and banner / caption / video boxes
- encoder.py      output encoder arguments derived from the ffmpeg build and source
- compose.py      ffmpeg filter graph + runner with progress
- pipeline.py     one job end to end: link or file -> layout -> captions -> MP4
- hosting.py      where the app runs: hosted flag, the CPUs a container really allows
- cookies_export.py  one site's login cookies from a local browser -> cookies.txt for a server
- server_defaults.py the defaults/ folder a hosted copy without a disk starts from
- text/           caption rendering (per-script font fallback, wrapping, PNG)
- ui/             the Streamlit tabs; app.py at the project root is the entry point
"""
