"""Frozen-app entry point.

The desktop build has no window of its own: it starts the local server and
hands the interface to the user's browser. That keeps one implementation of
the UI for both the installed app and the "run from source" path, and avoids
shipping a second rendering engine inside the installer.

Anything that goes wrong before the browser opens would otherwise be invisible
in a windowed build, so early failures are written to a log file and shown in a
native dialog.
"""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import traceback
from pathlib import Path


def _log_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "TTS Studio"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TTS Studio" / "logs"
    else:
        base = Path.home() / ".local" / "state" / "tts-studio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _alert(title: str, message: str) -> None:
    """Best-effort native dialog; never raise from the error path itself."""
    try:
        if sys.platform == "darwin":
            script = (
                f'display dialog {message!r} with title {title!r} '
                'buttons {"OK"} default button "OK" with icon caution'
            )
            subprocess.run(["osascript", "-e", script], check=False, timeout=60)
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        else:
            print(f"{title}: {message}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    log_path = _log_dir() / "startup.log"
    try:
        # A windowed build has no usable stdio; give the server somewhere to write.
        if not sys.stdout or not sys.stdout.isatty():
            handle = log_path.open("a", encoding="utf-8", buffering=1)
            sys.stdout = handle
            sys.stderr = handle

        from ttsstudio.server import DEFAULT_PORT, serve

        serve(port=DEFAULT_PORT, open_browser=True)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        try:
            log_path.write_text(
                f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}\n", encoding="utf-8"
            )
        except OSError:
            pass
        _alert(
            "TTS Studio could not start",
            f"{type(exc).__name__}: {exc}\n\nDetails were written to:\n{log_path}",
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
