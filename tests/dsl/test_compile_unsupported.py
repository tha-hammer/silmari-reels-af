"""B12 → Tier-4: insert relevant / insert file / find relevant compile correctly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import DslWord, SourceRef, WordsSidecar
from reel_af.dsl.relevant import RelevantCandidate, RelevantRange

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def _dense_words() -> WordsSidecar:
    """Extend the fixture word stream with filler so search_relevant finds content."""
    base = json.loads((FIXTURES / "source.words.json").read_text())
    extra: list[dict] = []
    vocab = ["deep", "learning", "model", "training", "data", "network",
             "pattern", "reason", "scale", "trust", "feeling", "bug"]
    t, i = 9.0, 0
    while t < 21.0:
        extra.append({"w": vocab[i % len(vocab)], "start": round(t, 3),
                       "end": round(t + 0.25, 3), "conf": 0.90})
        t += 0.35
        i += 1
    t, i = 26.0, 0
    while t < 70.0:
        extra.append({"w": vocab[i % len(vocab)], "start": round(t, 3),
                       "end": round(t + 0.25, 3), "conf": 0.90})
        t += 0.35
        i += 1
    all_words = sorted(base["words"] + extra, key=lambda w: w["start"])
    return WordsSidecar.model_validate({"schema_version": "1", "words": all_words})


def test_insert_relevant_compiles_to_source_segments():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert relevant 5]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = _dense_words()
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status in ("ok", "warning"), f"expected ok/warning, got {res.status}: {res.diagnostics}"
    assert res.plan is not None
    assert len(res.plan.segments) >= 3
    codes = [d.code for d in res.diagnostics]
    assert "UNSUPPORTED_INSERT" not in codes


def test_insert_file_compiles_to_source_segment(tmp_path):
    candidate = RelevantCandidate(
        stem="rel_01",
        ranges=[RelevantRange(start_s=30.0, end_s=40.0, text="deep learning model")],
        total_duration_s=10.0,
    )
    relevant_dir = tmp_path / "relevant"
    relevant_dir.mkdir()
    (relevant_dir / "rel_01.json").write_text(candidate.model_dump_json(indent=2))

    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert file rel_01]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = _dense_words()
    res = compile_composite(
        doc, words, SourceRef(source_url=SOURCE_URL), relevant_dir=relevant_dir,
    )

    assert res.status in ("ok", "warning"), f"expected ok/warning, got {res.status}: {res.diagnostics}"
    assert res.plan is not None
    assert len(res.plan.segments) >= 3
    codes = [d.code for d in res.diagnostics]
    assert "UNSUPPORTED_INSERT" not in codes


def test_insert_file_missing_candidate_errors(tmp_path):
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert file nonexistent]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = _dense_words()
    res = compile_composite(
        doc, words, SourceRef(source_url=SOURCE_URL), relevant_dir=tmp_path,
    )

    assert res.status == "error"
    codes = [d.code for d in res.diagnostics]
    assert "CANDIDATE_NOT_FOUND" in codes


def test_find_relevant_is_noop():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[find relevant 30 x5]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = _dense_words()
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status in ("ok", "warning"), f"expected ok/warning, got {res.status}: {res.diagnostics}"
    assert res.plan is not None
    assert len(res.plan.segments) == 2
    codes = [d.code for d in res.diagnostics]
    assert "UNSUPPORTED_FIND" not in codes
