"""Runtime bootstrap: install engines on demand, from inside the app.

A shipped installer cannot carry PyTorch and several gigabytes of model weights,
and most users only ever want one engine. So the bundle stays small and this
module builds what the user actually asks for:

  1. fetch ``uv`` (one static binary, no Python needed) into the app data dir
  2. have uv provision a private CPython and a virtualenv per engine
  3. pip-install that engine's packages into its own venv
  4. download any loose model files the backend needs

Engines are then run as subprocesses against their own interpreter, which keeps
incompatible dependency sets (MLX vs PyTorch vs ONNX) from ever meeting.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from . import net, paths
from .engines import Backend, backend_for

PYTHON_VERSION = "3.12"
UV_VERSION = "0.12.5"
Progress = Callable[[str, float | None], None]


def _noop(_msg: str, _frac: float | None = None) -> None:
    pass


# --------------------------------------------------------------------------
# uv
# --------------------------------------------------------------------------


def _uv_asset() -> tuple[str, str]:
    """(asset filename, name of the uv executable inside it) for this machine."""
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"uv-{arch}-apple-darwin.tar.gz", "uv"
    if sys.platform == "win32":
        arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
        suffix = "pc-windows-msvc" if arch == "x86_64" else "pc-windows-msvc"
        return f"uv-{arch}-{suffix}.zip", "uv.exe"
    arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    return f"uv-{arch}-unknown-linux-gnu.tar.gz", "uv"


def uv_path() -> Path:
    exe = "uv.exe" if sys.platform == "win32" else "uv"
    return paths.bin_dir() / exe


def find_uv() -> Path | None:
    """Prefer our own copy, but reuse a system uv when the user already has one."""
    own = uv_path()
    if own.exists():
        return own
    found = shutil.which("uv")
    return Path(found) if found else None


def ensure_uv(progress: Progress = _noop) -> Path:
    existing = find_uv()
    if existing:
        return existing

    asset, exe_name = _uv_asset()
    url = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{asset}"
    paths.bin_dir().mkdir(parents=True, exist_ok=True)
    progress(f"Downloading uv {UV_VERSION}…", 0.0)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset
        _download(url, archive, progress, label="uv")
        extract_to = Path(tmp) / "x"
        extract_to.mkdir()
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_to)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(extract_to)
        src = next((p for p in extract_to.rglob(exe_name) if p.is_file()), None)
        if src is None:
            raise RuntimeError(f"uv executable not found inside {asset}")
        dest = uv_path()
        shutil.copy2(src, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    progress("uv ready.", 1.0)
    return uv_path()


def _download(url: str, dest: Path, progress: Progress = _noop, *, label: str = "") -> Path:
    """Stream a file to disk, reporting fractional progress when size is known."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "TTS-Studio"})
    with urllib.request.urlopen(req, timeout=60, context=net.ssl_context()) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        chunk_mb = 1024 * 256
        with part.open("wb") as fh:
            while True:
                block = resp.read(chunk_mb)
                if not block:
                    break
                fh.write(block)
                got += len(block)
                if total:
                    frac = got / total
                    progress(f"{label or dest.name}: {got // 1_048_576} / {total // 1_048_576} MB", frac)
                else:
                    progress(f"{label or dest.name}: {got // 1_048_576} MB", None)
    part.replace(dest)
    return dest


# --------------------------------------------------------------------------
# Engine environments
# --------------------------------------------------------------------------


def engine_dir(engine_id: str) -> Path:
    return paths.engines_dir() / engine_id


def engine_python(engine_id: str) -> Path:
    venv = engine_dir(engine_id)
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def model_dir(engine_id: str, backend_id: str) -> Path:
    return paths.models_dir() / f"{engine_id}-{backend_id}"


def _marker(engine_id: str) -> Path:
    return engine_dir(engine_id) / ".installed.json"


def is_installed(engine_id: str) -> bool:
    backend = backend_for(engine_id)
    if backend is None:
        return True  # cloud engines need nothing
    if not engine_python(engine_id).exists() or not _marker(engine_id).exists():
        return False
    try:
        info = json.loads(_marker(engine_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if info.get("backend") != backend.id:
        return False
    target = model_dir(engine_id, backend.id)
    return all((target / name).exists() for _url, name, _size in backend.files)


def engine_status(engine_id: str) -> dict:
    backend = backend_for(engine_id)
    if backend is None:
        return {"installed": True, "backend": None}
    return {
        "installed": is_installed(engine_id),
        "backend": backend.id,
        "backend_label": backend.label,
        "accelerator": backend.accelerator,
        "approx_mb": backend.approx_mb,
    }


def _run_streaming(cmd: list[str], progress: Progress, *, env: dict | None = None) -> None:
    """Run a command, forwarding each output line to the progress callback."""
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        creationflags=creationflags,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail.append(line)
            tail[:] = tail[-40:]
            progress(line, None)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed ({code}):\n" + "\n".join(tail[-12:]))


def install_engine(engine_id: str, progress: Progress = _noop) -> dict:
    """Provision an engine end to end. Safe to re-run — it repairs a partial install."""
    backend = backend_for(engine_id)
    if backend is None:
        return {"installed": True, "backend": None}

    paths.ensure_dirs()
    uv = ensure_uv(progress)

    env = os.environ.copy()
    env["UV_PYTHON_INSTALL_DIR"] = str(paths.data_dir() / "python")
    env["UV_CACHE_DIR"] = str(paths.data_dir() / "uv-cache")

    progress(f"Preparing Python {PYTHON_VERSION}…", None)
    _run_streaming([str(uv), "python", "install", PYTHON_VERSION], progress, env=env)

    venv = engine_dir(engine_id)
    if not engine_python(engine_id).exists():
        progress("Creating isolated environment…", None)
        _run_streaming([str(uv), "venv", "--python", PYTHON_VERSION, str(venv)], progress, env=env)

    progress(f"Installing {engine_id} ({backend.label})… this can take several minutes.", None)
    _run_streaming(
        [str(uv), "pip", "install", "--python", str(engine_python(engine_id)), *backend.packages],
        progress,
        env=env,
    )

    if backend.files:
        target = model_dir(engine_id, backend.id)
        target.mkdir(parents=True, exist_ok=True)
        for url, name, _size in backend.files:
            dest = target / name
            if dest.exists():
                progress(f"{name} already downloaded.", None)
                continue
            progress(f"Downloading {name}…", 0.0)
            _download(url, dest, progress, label=name)

    _marker(engine_id).write_text(
        json.dumps({"backend": backend.id, "packages": backend.packages, "python": PYTHON_VERSION}, indent=2),
        encoding="utf-8",
    )
    progress(f"{engine_id} is ready.", 1.0)
    return engine_status(engine_id)


def uninstall_engine(engine_id: str) -> None:
    shutil.rmtree(engine_dir(engine_id), ignore_errors=True)
    backend = backend_for(engine_id)
    if backend is not None:
        shutil.rmtree(model_dir(engine_id, backend.id), ignore_errors=True)


def worker_env(engine_id: str) -> dict:
    """Environment for a worker subprocess — keeps HF downloads in our cache."""
    env = os.environ.copy()
    env["HF_HOME"] = str(paths.hf_cache_dir())
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    backend = backend_for(engine_id)
    if backend is not None:
        env["TTS_STUDIO_MODEL_DIR"] = str(model_dir(engine_id, backend.id))
    return env


def worker_script(engine_id: str) -> Path:
    backend = backend_for(engine_id)
    if backend is None:
        raise RuntimeError(f"{engine_id} has no local worker")
    return paths.resource_path("workers", backend.worker)


def disk_usage() -> dict:
    """Bytes used by each installed engine — shown in the model manager."""
    out: dict[str, int] = {}
    for base in (paths.engines_dir(), paths.models_dir(), paths.hf_cache_dir()):
        if not base.exists():
            continue
        for child in base.iterdir():
            if not child.is_dir():
                continue
            size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            out[child.name] = out.get(child.name, 0) + size
    return out
