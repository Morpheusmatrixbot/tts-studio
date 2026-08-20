"""Entry point: `python -m ttsstudio` and the frozen desktop app."""

from __future__ import annotations

import argparse
import multiprocessing

from .server import DEFAULT_PORT, serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="tts-studio", description="TTS Studio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    args = parser.parse_args()
    serve(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    # PyInstaller re-executes the bundle for child processes without this.
    multiprocessing.freeze_support()
    main()
