"""A long-lived engine process, kept alive for the duration of one job.

Loading a TTS model costs about four seconds. A novel is roughly two thousand
chunks, so spawning a process per chunk spends about two hours reloading weights
that never change — measured against sixteen minutes of actual synthesis for
Kokoro. This keeps one process per job instead.

The worker is deliberately not shared between jobs: a job pins one engine, one
voice and one language, and letting a process outlive the job that configured it
invites a stale model answering for settings it was never given.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from . import runtime

# Model load on a cold filesystem cache is the slow one; generation is bounded
# by the chunk size the app enforces.
READY_TIMEOUT_S = 600.0
CHUNK_TIMEOUT_S = 900.0


class WorkerError(RuntimeError):
    pass


class EngineWorker:
    """One engine subprocess speaking newline-delimited JSON."""

    def __init__(self, engine_id: str, base_request: dict):
        self.engine_id = engine_id
        self.base_request = base_request
        self._proc: subprocess.Popen | None = None
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ready = False

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        python = runtime.engine_python(self.engine_id)
        if not python.exists():
            raise WorkerError(
                f"{self.engine_id} is not installed yet. Open the Engines tab and install it."
            )
        script = runtime.worker_script(self.engine_id)
        if not script.exists():
            raise WorkerError(f"Worker script missing: {script}")

        self._proc = subprocess.Popen(
            [str(python), str(script), "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=runtime.worker_env(self.engine_id),
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        # Model libraries are chatty. Nobody reading stderr means a full pipe and
        # a worker blocked forever on its own log line, so drain it continuously.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name=f"{self.engine_id}-stderr"
        )
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr_tail.append(line)

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def __enter__(self) -> "EngineWorker":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- requests -------------------------------------------------------

    def _errors(self) -> str:
        return "\n".join(list(self._stderr_tail)[-12:]) or "no output"

    def _read_reply(self, timeout: float) -> dict:
        """Read one JSON line, tolerating stray output the worker did not suppress."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise WorkerError(f"{self.engine_id} worker is not running")

        result: dict = {}
        error: list[str] = []

        def reader() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        result.update(json.loads(line))
                        return
                    except json.JSONDecodeError:
                        self._stderr_tail.append(line)
                error.append("worker closed its output")
            except (OSError, ValueError) as exc:
                error.append(str(exc))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            self.stop()
            raise WorkerError(f"{self.engine_id} timed out after {timeout:.0f}s")
        if error or not result:
            code = proc.poll()
            raise WorkerError(
                f"{self.engine_id} worker stopped"
                + (f" (exit {code})" if code is not None else "")
                + f":\n{self._errors()}"
            )
        return result

    def synth(self, text: str, out_path: Path) -> Path:
        """Synthesise one chunk. The first call also pays for the model load."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise WorkerError(f"{self.engine_id} worker is not running:\n{self._errors()}")

            request = dict(self.base_request)
            request["text"] = text
            request["out"] = str(out_path)
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise WorkerError(
                    f"{self.engine_id} worker stopped accepting work:\n{self._errors()}"
                ) from exc

            if not self._ready:
                # The worker announces readiness once, after loading the model.
                first = self._read_reply(READY_TIMEOUT_S)
                if first.get("ready"):
                    self._ready = True
                    reply = self._read_reply(CHUNK_TIMEOUT_S)
                else:
                    reply = first
            else:
                reply = self._read_reply(CHUNK_TIMEOUT_S)

        if not reply.get("ok"):
            raise WorkerError(reply.get("error") or f"{self.engine_id} failed:\n{self._errors()}")
        if not out_path.exists() or out_path.stat().st_size < 100:
            raise WorkerError(f"{self.engine_id} produced no audio for a chunk")
        return out_path
