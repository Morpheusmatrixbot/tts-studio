"""Background narration jobs, with honest progress and time estimates.

An audiobook can run for hours, so the UI needs more than a spinner. Progress is
measured in characters rather than chunks, because a chunk holding one short
sentence and a chunk holding a full paragraph are not the same amount of work;
counting chunks makes the bar lurch. The remaining time comes from a rolling
throughput of recently finished chunks, with the first chunk excluded — it also
paid to load the model, which happens once and would otherwise inflate every
estimate that follows it.

Progress survives a browser refresh: state lives here, the page polls it.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import audio, paths, runtime, settings, synth

_JOBS: dict[str, "Job"] = {}
_LOCK = threading.Lock()
# Keep enough history for the UI to still show the last finished run.
_MAX_JOBS = 24


def _slug(text: str) -> str:
    import re

    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return (s or "narration")[:60]


@dataclass
class Job:
    id: str
    kind: str  # "narrate" | "install"
    state: str = "queued"  # queued | running | done | error | cancelled
    label: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    total_chars: int = 0
    done_chars: int = 0
    total_chunks: int = 0
    done_chunks: int = 0
    audio_seconds: float = 0.0

    # install-only: 0..1 when the step reports a fraction
    step_fraction: float | None = None

    log: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict | None = None
    cancel: bool = False

    _samples: deque = field(default_factory=lambda: deque(maxlen=12))

    def add_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {message}")
        del self.log[:-400]

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def rate_chars_per_sec(self) -> float | None:
        """Throughput from recent chunks; None until there is real evidence."""
        if not self._samples:
            return None
        chars = sum(c for c, _d in self._samples)
        secs = sum(d for _c, d in self._samples)
        if secs <= 0.01 or chars <= 0:
            return None
        return chars / secs

    def eta_seconds(self) -> float | None:
        if self.state in {"done", "cancelled"}:
            return 0.0
        rate = self.rate_chars_per_sec()
        remaining = max(0, self.total_chars - self.done_chars)
        if rate is None or rate <= 0:
            return None
        return remaining / rate

    def percent(self) -> float:
        if self.kind == "install":
            if self.state == "done":
                return 100.0
            return round((self.step_fraction or 0.0) * 100.0, 1)
        if self.total_chars <= 0:
            return 0.0
        return round(min(100.0, 100.0 * self.done_chars / self.total_chars), 1)

    def snapshot(self) -> dict:
        eta = self.eta_seconds()
        elapsed = self.elapsed()
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "label": self.label,
            "percent": self.percent(),
            "done_chunks": self.done_chunks,
            "total_chunks": self.total_chunks,
            "done_chars": self.done_chars,
            "total_chars": self.total_chars,
            "audio_seconds": round(self.audio_seconds, 1),
            "elapsed_seconds": round(elapsed, 1),
            "eta_seconds": None if eta is None else round(eta, 1),
            "total_estimate_seconds": None if eta is None else round(elapsed + eta, 1),
            "step_fraction": self.step_fraction,
            "log": self.log[-120:],
            "error": self.error,
            "result": self.result,
        }


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def snapshot(job_id: str) -> dict | None:
    job = get(job_id)
    return job.snapshot() if job else None


def cancel(job_id: str) -> bool:
    job = get(job_id)
    if job and job.state in {"queued", "running"}:
        job.cancel = True
        return True
    return False


def _register(job: Job) -> None:
    with _LOCK:
        _JOBS[job.id] = job
        if len(_JOBS) > _MAX_JOBS:
            done = sorted(
                (j for j in _JOBS.values() if j.state in {"done", "error", "cancelled"}),
                key=lambda j: j.finished_at or j.created_at,
            )
            for old in done[: max(0, len(_JOBS) - _MAX_JOBS)]:
                _JOBS.pop(old.id, None)


def _start(job: Job, target, *args) -> str:
    _register(job)

    def runner() -> None:
        job.state = "running"
        job.started_at = time.time()
        try:
            target(job, *args)
            if job.cancel:
                job.state = "cancelled"
                job.add_log("Cancelled.")
            else:
                job.state = "done"
        except Exception as exc:  # noqa: BLE001 — surface to the UI, never crash the server
            job.state = "error"
            job.error = str(exc)
            job.add_log(f"ERROR: {exc}")
            _write_crash_log(job, exc)
        finally:
            job.finished_at = time.time()

    threading.Thread(target=runner, daemon=True, name=f"job-{job.id}").start()
    return job.id


def _write_crash_log(job: Job, exc: Exception) -> None:
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        log = paths.logs_dir() / f"error-{job.id}.log"
        log.write_text(
            f"{datetime.now().isoformat()}\n{exc}\n\n{traceback.format_exc()}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
# Engine installation
# --------------------------------------------------------------------------


def start_install(engine_id: str) -> str:
    job = Job(id=uuid.uuid4().hex[:12], kind="install", label=f"Install {engine_id}")
    return _start(job, _run_install, engine_id)


def _run_install(job: Job, engine_id: str) -> None:
    def progress(message: str, fraction: float | None) -> None:
        if job.cancel:
            raise RuntimeError("Cancelled by user")
        if fraction is not None:
            job.step_fraction = max(0.0, min(1.0, fraction))
        job.add_log(message)

    job.add_log(f"Installing {engine_id}…")
    status = runtime.install_engine(engine_id, progress)
    job.step_fraction = 1.0
    job.result = {"engine": engine_id, "status": status}
    job.add_log("Installation finished.")


# --------------------------------------------------------------------------
# Narration
# --------------------------------------------------------------------------


def start_narration(payload: dict) -> str:
    label = payload.get("output_name") or payload.get("title") or "Narration"
    job = Job(id=uuid.uuid4().hex[:12], kind="narrate", label=str(label))
    return _start(job, _run_narration, payload)


def _sections_from(payload: dict) -> tuple[str, list[dict]]:
    from .extract import normalize, split_paragraphs

    if payload.get("sections"):
        out = []
        for i, s in enumerate(payload["sections"]):
            paras = [p for p in (s.get("paragraphs") or []) if p and p.strip()]
            if paras:
                out.append(
                    {
                        "id": s.get("id") or f"section-{i + 1:02d}",
                        "heading": s.get("heading") or "",
                        "paragraphs": paras,
                    }
                )
        if not out:
            raise ValueError("The document has no readable text.")
        return str(payload.get("title") or ""), out

    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("No text to narrate.")
    paras = split_paragraphs(text) or [normalize(text)]
    return "", [{"id": "section-01", "heading": "", "paragraphs": [p for p in paras if p]}]


def _resolve_output_dir(payload: dict) -> Path:
    raw = str(payload.get("output_dir") or "").strip()
    base = Path(raw).expanduser() if raw else paths.default_output_dir()
    return base.resolve()


def _run_narration(job: Job, payload: dict) -> None:
    cfg = {
        "engine": str(payload.get("engine") or "kokoro"),
        "voice": payload.get("voice") or "",
        "voice_sample": payload.get("voice_sample") or "",
        "lang": payload.get("lang") or "",
        "speed": float(payload.get("speed") or 1.0),
        "model_id": payload.get("model_id") or "",
        "temperature": float(payload.get("temperature") or 0.55),
        "repetition_penalty": float(payload.get("repetition_penalty") or 1.65),
        "rate": payload.get("rate") or "+0%",
        "pitch": payload.get("pitch") or "+0Hz",
    }
    synth.preflight(cfg)

    title, sections = _sections_from(payload)
    chunk_words = max(20, min(90, int(payload.get("chunk_words") or 55)))

    plan: list[dict] = []
    for section in sections:
        source = (
            [section["heading"], *section["paragraphs"]]
            if section.get("heading")
            else section["paragraphs"]
        )
        pieces = audio.chunk_paragraphs(source, max_words=chunk_words)
        if pieces:
            plan.append({"id": section["id"], "heading": section.get("heading", ""), "chunks": pieces})
    if not plan:
        raise ValueError("Nothing to narrate after cleaning up the text.")

    job.total_chunks = sum(len(s["chunks"]) for s in plan)
    job.total_chars = sum(len(t) for s in plan for t, _ in s["chunks"])

    out_root = _resolve_output_dir(payload)
    name = _slug(payload.get("output_name") or title or "narration")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_dir = out_root / f"{name}-{stamp}"
    chunks_dir = job_dir / "chunks"
    chapters_dir = job_dir / "chapters"
    for d in (chunks_dir, chapters_dir):
        d.mkdir(parents=True, exist_ok=True)

    job.add_log(
        f"{cfg['engine']} · {len(plan)} section(s) · {job.total_chunks} chunks · "
        f"{job.total_chars:,} characters"
    )
    job.add_log(f"Output: {job_dir}")
    settings.save({"output_dir": str(out_root), "last_engine": cfg["engine"]})

    chapters: list[dict] = []
    for section in plan:
        if job.cancel:
            break
        section_dir = chunks_dir / section["id"]
        section_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        gaps: list[float] = []

        for index, (text, new_paragraph) in enumerate(section["chunks"], start=1):
            if job.cancel:
                break
            started = time.time()
            target = section_dir / f"{index:04d}"
            produced = synth.synth_chunk(text, target, cfg)
            took = time.time() - started

            written.append(produced)
            gaps.append(audio.GAP_PARAGRAPH if new_paragraph else audio.GAP_SENTENCE)
            job.done_chunks += 1
            job.done_chars += len(text)
            job.audio_seconds += audio.duration_of(produced)
            # The first chunk also paid for model loading — excluding it keeps
            # the ETA from starting out wildly pessimistic.
            if job.done_chunks > 1 or job.total_chunks == 1:
                job._samples.append((len(text), max(took, 0.01)))

            if job.done_chunks % 5 == 0 or job.done_chunks == job.total_chunks:
                eta = job.eta_seconds()
                job.add_log(
                    f"{job.done_chunks}/{job.total_chunks} chunks · {job.percent():.0f}%"
                    + (f" · ~{_fmt(eta)} left" if eta else "")
                )

        if not written:
            continue
        merged, sr = audio.concat(written, gaps)
        chapter_path = chapters_dir / f"{section['id']}.wav"
        audio.write(chapter_path, merged, sr)
        chapters.append(
            {
                "id": section["id"],
                "heading": section.get("heading") or section["id"],
                "path": str(chapter_path),
                "seconds": round(len(merged) / sr, 1),
            }
        )
        job.add_log(f"Finished {section['id']} ({_fmt(len(merged) / sr)})")

    if job.cancel:
        job.result = {"dir": str(job_dir), "partial": True, "chapters": chapters}
        return
    if not chapters:
        raise RuntimeError("No audio was produced.")

    job.add_log("Joining the full narration…")
    chapter_paths = [Path(c["path"]) for c in chapters]
    full, sr = audio.concat(chapter_paths, [audio.GAP_SECTION] * len(chapter_paths))
    wav_path = audio.write(job_dir / f"{name}.wav", full, sr)

    mp3_path = None
    if audio.mp3_supported():
        try:
            mp3_path = audio.write(job_dir / f"{name}.mp3", full, sr, fmt="MP3")
        except Exception as exc:  # noqa: BLE001 — WAV already succeeded
            job.add_log(f"MP3 export skipped: {exc}")

    total_seconds = len(full) / sr
    manifest = {
        "title": title or name,
        "engine": cfg["engine"],
        "voice": cfg.get("voice") or cfg.get("voice_sample"),
        "created": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": round(total_seconds, 1),
        "chapters": chapters,
    }
    (job_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if not payload.get("keep_chunks"):
        shutil.rmtree(chunks_dir, ignore_errors=True)

    job.result = {
        "dir": str(job_dir),
        "wav": str(wav_path),
        "mp3": str(mp3_path) if mp3_path else None,
        "chapters": chapters,
        "duration_seconds": round(total_seconds, 1),
    }
    job.add_log(f"Done — {_fmt(total_seconds)} of audio in {_fmt(job.elapsed())}.")


def _fmt(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "0s"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
