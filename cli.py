"""Command line: frame links and/or video files with the app's saved Settings.

  .venv/bin/python cli.py https://www.instagram.com/reel/XXXX/ [more links or files]
  .venv/bin/python cli.py --settings other.json clip.mp4

An argument naming an existing file is used as it is; anything else is treated
as a link and downloaded. Each finished video's path is printed on stdout;
progress and failures go to stderr. Exit status 0 = all done, 1 = something failed.

Not in here: the processing itself (reel_framer/pipeline.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reel_framer import pipeline
from reel_framer import settings as store
from reel_framer.compose import ComposeError
from reel_framer.downloader import DownloadError
from reel_framer.encoder import EncoderError
from reel_framer.media_probe import ProbeError

_FAILURES = (DownloadError, pipeline.JobError, ComposeError, ProbeError, EncoderError, OSError)


def _report(stage: str, fraction: float | None) -> None:
    text = stage if fraction is None else f"{stage} {fraction:.0%}"
    print(f"\r  {text}".ljust(40), end="", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frame reels/videos with your saved banners and captions.")
    parser.add_argument("items", nargs="+", help="links or video files")
    parser.add_argument("--settings", type=Path, help="settings JSON file (default: the app's saved settings)")
    args = parser.parse_args(argv)
    current = store.load(args.settings) if args.settings else store.load()

    failed = False
    for item in args.items:
        print(item, file=sys.stderr)
        try:
            if Path(item).is_file():
                sources = [(Path(item), Path(item).stem)]
            else:
                sources = [(d.path, d.title) for d in pipeline.download_link(item, current, _report)]
            for path, title in sources:
                result = pipeline.process_file(path, current, _report, title=title)
                print(file=sys.stderr)
                print(result.output)
        except _FAILURES as exc:
            failed = True
            print(f"\n  FAILED: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
