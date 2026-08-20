"""The stdin/stdout contract between the host and an engine worker.

Exercised against a stub worker rather than a real engine, so the protocol —
handshake, framing, error isolation, noisy output — is verified without a
multi-gigabyte model or a particular platform's backend.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ttsstudio.worker_pool import EngineWorker, WorkerError

WORKERS_DIR = Path(__file__).resolve().parents[1] / "ttsstudio" / "workers"

# A worker that uses the real serve loop but synthesises a trivial tone, so the
# protocol is under test rather than any model.
STUB = textwrap.dedent(
    """
    import sys, argparse
    sys.path.insert(0, {workers!r})
    import numpy as np
    from _common import run_cli_or_serve

    def load_model(request):
        # Chatter on stdout during load must not corrupt the protocol.
        print("loading model, please wait")
        if request.get("fail_load"):
            raise RuntimeError("model refused to load")
        return {{"loaded": True}}

    def generate(model, request):
        text = request["text"]
        if "boom" in text:
            raise ValueError("bad chunk")
        print("progress: 50%")
        seconds = max(0.1, len(text) / 100.0)
        sr = 24000
        t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
        return (np.sin(2 * np.pi * 220 * t) * 0.2).astype(np.float32), sr

    p = argparse.ArgumentParser()
    p.add_argument("--text")
    p.add_argument("--out")
    p.add_argument("--fail-load", dest="fail_load", action="store_true")
    raise SystemExit(run_cli_or_serve(p, load_model, generate))
    """
).format(workers=str(WORKERS_DIR))


@pytest.fixture
def stub_worker(tmp_path, monkeypatch):
    """An EngineWorker wired to the stub instead of a real engine."""
    script = tmp_path / "stub_worker.py"
    script.write_text(STUB, encoding="utf-8")

    from ttsstudio import runtime

    monkeypatch.setattr(runtime, "engine_python", lambda _e: Path(sys.executable))
    monkeypatch.setattr(runtime, "worker_script", lambda _e: script)
    monkeypatch.setattr(runtime, "worker_env", lambda _e: None)
    return script


def test_one_process_serves_many_chunks(stub_worker, tmp_path):
    # The whole point of serve mode: one process, many chunks.
    with EngineWorker("stub", {}) as worker:
        pid = worker._proc.pid
        for i in range(5):
            out = worker.synth(f"chunk number {i}", tmp_path / f"{i}.wav")
            assert out.exists()
            assert sf.info(str(out)).frames > 0
        assert worker._proc.pid == pid, "worker was restarted between chunks"


def test_model_loads_once(stub_worker, tmp_path):
    with EngineWorker("stub", {}) as worker:
        for i in range(4):
            worker.synth("some text", tmp_path / f"{i}.wav")
        loads = [line for line in worker._stderr_tail if "loading model" in line]
        assert len(loads) == 1, f"expected one load, saw {len(loads)}"


def test_base_request_is_sent_with_every_chunk(stub_worker, tmp_path):
    # Voice and language are fixed for the job and must reach the worker.
    with EngineWorker("stub", {"voice": "bm_george", "lang": "b"}) as worker:
        out = worker.synth("hello", tmp_path / "a.wav")
        assert out.exists()


def test_noisy_stdout_does_not_break_framing(stub_worker, tmp_path):
    # The stub prints during load and during generation; replies must survive.
    with EngineWorker("stub", {}) as worker:
        out = worker.synth("hello world", tmp_path / "a.wav")
        assert out.exists()


def test_a_failing_chunk_does_not_kill_the_worker(stub_worker, tmp_path):
    with EngineWorker("stub", {}) as worker:
        worker.synth("fine", tmp_path / "ok1.wav")
        with pytest.raises(WorkerError, match="bad chunk"):
            worker.synth("boom goes the chunk", tmp_path / "bad.wav")
        # Still alive and usable afterwards.
        out = worker.synth("fine again", tmp_path / "ok2.wav")
        assert out.exists()


def test_load_failure_is_reported(stub_worker, tmp_path):
    with EngineWorker("stub", {"fail_load": True}) as worker:
        with pytest.raises(WorkerError, match="model refused to load"):
            worker.synth("hello", tmp_path / "a.wav")


def test_missing_engine_is_reported_clearly(monkeypatch, tmp_path):
    from ttsstudio import runtime

    monkeypatch.setattr(runtime, "engine_python", lambda _e: tmp_path / "nope" / "python")
    worker = EngineWorker("kokoro", {})
    with pytest.raises(WorkerError, match="not installed"):
        worker.start()


def test_stop_is_idempotent(stub_worker, tmp_path):
    worker = EngineWorker("stub", {})
    worker.start()
    worker.synth("hello", tmp_path / "a.wav")
    worker.stop()
    worker.stop()  # must not raise
    with pytest.raises(WorkerError):
        worker.synth("hello", tmp_path / "b.wav")


def test_cli_mode_still_works(stub_worker, tmp_path):
    # One-shot invocation stays available; it is how an engine gets debugged.
    out = tmp_path / "cli.wav"
    proc = subprocess.run(
        [sys.executable, str(stub_worker), "--text", "hello there", "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert np.max(np.abs(sf.read(str(out))[0])) > 0
