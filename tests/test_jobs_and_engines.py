"""Engine selection, progress arithmetic and settings redaction."""

from __future__ import annotations

import time

import pytest

from ttsstudio import engines, jobs, settings
from ttsstudio.jobs import Job


# --------------------------------------------------------------------- engines


def test_every_local_engine_resolves_to_a_backend():
    for engine in engines.ENGINES:
        backend = engines.backend_for(engine.id)
        if engine.needs_install:
            assert backend is not None, f"{engine.id} has no backend on this platform"
            assert backend.worker.endswith(".py")
            assert backend.packages
        else:
            assert backend is None


def test_backend_choice_follows_the_platform(monkeypatch):
    monkeypatch.setattr(engines, "is_apple_silicon", lambda: True)
    assert engines.backend_for("kokoro").id == "mlx"
    assert engines.backend_for("chatterbox").id == "mlx"

    monkeypatch.setattr(engines, "is_apple_silicon", lambda: False)
    assert engines.backend_for("kokoro").id == "onnx"
    assert engines.backend_for("chatterbox").id == "torch"


def test_portable_kokoro_declares_its_weight_files():
    backend = engines.BACKENDS["kokoro"]["onnx"]
    names = {name for _url, name, _size in backend.files}
    assert names == {"kokoro-v1.0.onnx", "voices-v1.0.bin"}
    assert all(url.startswith("https://") for url, _n, _s in backend.files)


def test_kokoro_mlx_ships_the_spacy_model():
    # misaki calls spacy.load("en_core_web_sm"); pip alone does not provide it.
    packages = engines.BACKENDS["kokoro"]["mlx"].packages
    assert any("en_core_web_sm" in p for p in packages)


def test_worker_scripts_exist_on_disk():
    from ttsstudio import paths

    for table in engines.BACKENDS.values():
        for backend in table.values():
            assert (paths.resource_path("workers", backend.worker)).exists()


# ------------------------------------------------------------------ progress


def _job(total_chars: int, total_chunks: int) -> Job:
    job = Job(id="t", kind="narrate")
    job.total_chars = total_chars
    job.total_chunks = total_chunks
    job.started_at = time.time()
    return job


def test_percent_tracks_characters_not_chunks():
    # Two chunks of very different size must not each count for 50%.
    job = _job(total_chars=1000, total_chunks=2)
    job.done_chars = 100
    job.done_chunks = 1
    assert job.percent() == 10.0


def test_percent_is_zero_before_any_work():
    assert _job(0, 0).percent() == 0.0


def test_eta_is_unknown_until_a_sample_arrives():
    job = _job(1000, 10)
    assert job.eta_seconds() is None
    assert job.snapshot()["eta_seconds"] is None


def test_eta_extrapolates_from_throughput():
    job = _job(1000, 10)
    job.done_chars = 200
    # 200 characters took 4 seconds → 50 chars/s → 800 remaining → 16 s.
    job._samples.append((200, 4.0))
    assert job.rate_chars_per_sec() == pytest.approx(50.0)
    assert job.eta_seconds() == pytest.approx(16.0)


def test_eta_is_zero_once_finished():
    job = _job(100, 1)
    job.state = "done"
    assert job.eta_seconds() == 0.0


def test_snapshot_exposes_the_fields_the_ui_reads():
    job = _job(500, 5)
    job.done_chars, job.done_chunks = 100, 1
    job._samples.append((100, 2.0))
    snap = job.snapshot()
    for key in (
        "percent", "done_chunks", "total_chunks", "elapsed_seconds",
        "eta_seconds", "total_estimate_seconds", "audio_seconds", "state",
    ):
        assert key in snap
    assert snap["total_estimate_seconds"] >= snap["eta_seconds"]


def test_log_is_capped():
    job = _job(1, 1)
    for i in range(900):
        job.add_log(f"line {i}")
    assert len(job.log) <= 400
    assert "line 899" in job.log[-1]


def test_fmt_durations():
    assert jobs._fmt(0) == "0s"
    assert jobs._fmt(45) == "45s"
    assert jobs._fmt(90) == "1m 30s"
    assert jobs._fmt(3700) == "1h 01m"


def test_slug_is_filesystem_safe():
    assert jobs._slug("Война и мир: Том 1!") != ""
    assert "/" not in jobs._slug("a/b")
    assert jobs._slug("") == "narration"


# ------------------------------------------------------------------ settings


def test_settings_never_return_the_api_key():
    from ttsstudio.server import _redact

    out = _redact({"language": "ru", "elevenlabs_api_key": "sk-secret-value"})
    assert "elevenlabs_api_key" not in out
    assert out["has_elevenlabs_key"] is True
    assert "sk-secret-value" not in str(out)


def test_settings_roundtrip(tmp_path, monkeypatch):
    from ttsstudio import paths

    monkeypatch.setenv("TTS_STUDIO_HOME", str(tmp_path))
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    settings.save({"language": "ru", "chunk_words": 40})
    loaded = settings.load()
    assert loaded["language"] == "ru"
    assert loaded["chunk_words"] == 40


def test_settings_ignore_unknown_keys(tmp_path, monkeypatch):
    from ttsstudio import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    saved = settings.save({"language": "en", "not_a_real_setting": "x"})
    assert "not_a_real_setting" not in saved
