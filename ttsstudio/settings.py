"""User settings, stored outside the app bundle.

Holds the interface language, last-used output folder and the ElevenLabs API
key. The key lives in the user's own data directory and is never written into
the project, so a checkout of this repository carries no secrets.
"""

from __future__ import annotations

import json
import os
import threading

from . import paths

_LOCK = threading.Lock()
_DEFAULTS: dict = {
    "language": "en",
    "output_dir": "",
    "elevenlabs_api_key": "",
    "last_engine": "",
    "chunk_words": 55,
}


def load() -> dict:
    with _LOCK:
        data = dict(_DEFAULTS)
        path = paths.settings_path()
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    data.update({k: v for k, v in stored.items() if k in _DEFAULTS})
            except (OSError, json.JSONDecodeError):
                pass
        if not data["output_dir"]:
            data["output_dir"] = str(paths.default_output_dir())
        return data


def save(updates: dict) -> dict:
    with _LOCK:
        path = paths.settings_path()
        current = dict(_DEFAULTS)
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    current.update({k: v for k, v in stored.items() if k in _DEFAULTS})
            except (OSError, json.JSONDecodeError):
                pass
        current.update({k: v for k, v in updates.items() if k in _DEFAULTS})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return current


def elevenlabs_key() -> str:
    """Settings first, then the environment — so CI and power users can override."""
    key = str(load().get("elevenlabs_api_key") or "").strip()
    return key or os.environ.get("ELEVENLABS_API_KEY", "").strip()
