"""Caption rendering.

- faces.py       Face (file + collection index + variation axes) and loading it into Pillow
- coretext.py    macOS backend: CoreText picks the face for each run (the OS's own fallback)
- windows.py     Windows backend: the registry's fonts and Windows' own font-link fallback
- fontconfig.py  backend for machines with fontconfig (Linux servers)
- chain.py       the per-cluster rule both chain-building backends share (emoji, whitespace)
- backend.py     picks the backend for this machine; one interface for render.py and the UI
- render.py      caption text -> transparent RGBA image (fallback runs, wrapping, outline, box, fit)
"""
