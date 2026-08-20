"""Per-platform application paths.

Everything the app installs at runtime (the uv binary, engine virtualenvs,
model weights, settings) lives under one user-writable data directory, so an
uninstall is a single folder delete and a frozen bundle stays read-only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TTS Studio"
APP_SLUG = "tts-studio"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> Path:
    """Locate a file shipped with the app (works frozen and from source).

    PyInstaller unpacks bundled data to a temp dir exposed as ``sys._MEIPASS``;
    from source the package directory is the root.
    """
    base = Path(getattr(sys, "_MEIPASS", "")) if is_frozen() else Path(__file__).resolve().parent
    if is_frozen():
        # Data files are bundled under the package name to avoid collisions.
        candidate = base / "ttsstudio"
        base = candidate if candidate.exists() else base
    return base.joinpath(*parts)


def data_dir() -> Path:
    """User data root — engines, models, settings, logs."""
    override = os.environ.get("TTS_STUDIO_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_SLUG


def bin_dir() -> Path:
    return data_dir() / "bin"


def engines_dir() -> Path:
    return data_dir() / "engines"


def models_dir() -> Path:
    return data_dir() / "models"


def hf_cache_dir() -> Path:
    """Shared HuggingFace cache so two engines never download the same weights twice."""
    return data_dir() / "hf-cache"


def logs_dir() -> Path:
    return data_dir() / "logs"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def default_output_dir() -> Path:
    """Where narrations land unless the user picks somewhere else."""
    music = Path.home() / "Music"
    base = music if music.is_dir() else Path.home()
    return base / APP_NAME


def ensure_dirs() -> None:
    for d in (data_dir(), bin_dir(), engines_dir(), models_dir(), hf_cache_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
