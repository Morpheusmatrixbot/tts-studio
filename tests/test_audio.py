"""Chunking and stitching — the parts that decide how a book is cut up."""

from __future__ import annotations

import numpy as np
import pytest

from ttsstudio import audio


def test_clean_closes_an_unbalanced_quote():
    # An unclosed quote makes autoregressive models restart the clause.
    assert audio.clean('He said "hello').endswith('"')
    assert audio.clean('He said "hello"').count('"') == 2


def test_clean_collapses_whitespace():
    assert audio.clean("a  \n b\t c") == "a b c"


def test_chunk_respects_word_budget():
    paragraph = " ".join(f"word{i}." for i in range(200))
    chunks = audio.chunk_paragraphs([paragraph], max_words=20)
    assert chunks
    for text, _ in chunks:
        # A single sentence may overshoot slightly, but never wildly.
        assert len(text.split()) <= 30


def test_chunk_marks_paragraph_boundaries():
    paras = ["First paragraph here.", "Second paragraph here.", "Third one."]
    chunks = audio.chunk_paragraphs(paras, max_words=4)
    assert len(chunks) >= 3
    assert chunks[0][1] is True  # first chunk always starts a paragraph


def test_chunk_splits_a_sentence_with_no_terminal_punctuation():
    # 300 words with no full stop must still be broken up, or the engine chokes.
    runaway = " ".join(["word"] * 300)
    chunks = audio.chunk_paragraphs([runaway], max_words=40)
    assert len(chunks) > 1
    assert all(len(t.split()) <= 60 for t, _ in chunks)


def test_chunk_ignores_empty_paragraphs():
    assert audio.chunk_paragraphs(["", "   ", "\n"], max_words=30) == []


def test_resample_changes_length_proportionally():
    signal = np.sin(np.linspace(0, 20, 1000)).astype(np.float32)
    out = audio.resample(signal, 24000, 48000)
    assert len(out) == pytest.approx(2000, rel=0.01)


def test_resample_is_a_noop_at_the_same_rate():
    signal = np.zeros(10, dtype=np.float32)
    assert audio.resample(signal, 24000, 24000) is signal


def test_concat_inserts_the_requested_gaps(tmp_path):
    sr = 24000
    tone = np.sin(np.linspace(0, 50, sr)).astype(np.float32) * 0.5  # 1 second
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.wav"
        audio.write(p, tone, sr)
        paths.append(p)

    merged, out_sr = audio.concat(paths, [0.0, 500.0, 500.0])
    assert out_sr == sr
    # 3 seconds of tone plus two 500 ms gaps.
    assert len(merged) / sr == pytest.approx(4.0, abs=0.05)


def test_write_and_read_roundtrip(tmp_path):
    sr = 24000
    tone = (np.sin(np.linspace(0, 100, sr)) * 0.4).astype(np.float32)
    path = audio.write(tmp_path / "a.wav", tone, sr)
    assert path.exists()
    back, back_sr = audio.read_mono(path)
    assert back_sr == sr
    assert len(back) == len(tone)
    assert audio.duration_of(path) == pytest.approx(1.0, abs=0.01)


def test_write_normalises_a_hot_signal(tmp_path):
    sr = 24000
    loud = (np.ones(sr) * 4.0).astype(np.float32)
    path = audio.write(tmp_path / "loud.wav", loud, sr)
    back, _ = audio.read_mono(path)
    assert np.max(np.abs(back)) <= 1.0


@pytest.mark.skipif(not audio.mp3_supported(), reason="libsndfile without MP3 support")
def test_mp3_export(tmp_path):
    sr = 24000
    tone = (np.sin(np.linspace(0, 100, sr)) * 0.4).astype(np.float32)
    path = audio.write(tmp_path / "a.mp3", tone, sr, fmt="MP3")
    assert path.exists() and path.stat().st_size > 500
