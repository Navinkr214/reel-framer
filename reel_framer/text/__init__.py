"""Caption rendering.

- faces.py       Face (file + collection index + variation axes) and loading it into Pillow
- coretext.py    macOS backend: CoreText picks the face for each run (the OS's own fallback)
- fontconfig.py  backend for machines with fontconfig (Linux servers)
- backend.py     picks the backend for this machine; one interface for render.py and the UI
- render.py      caption text -> transparent RGBA image (fallback runs, wrapping, outline, box, fit)
"""
