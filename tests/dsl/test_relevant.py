"""Tests for the relevant content search module (RED → GREEN TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.models import DslWord, WordsSidecar
from reel_af.dsl.relevant import (
    RelevantCandidate,
    RelevantRange,
    find_candidates,
    load_candidate,
    search_relevant,
)


def _make_dense_words(
    vocab: list[str] | None = None,
    duration_s: float = 60.0,
    words_per_second: float = 3.0,
) -> WordsSidecar:
    if vocab is None:
        vocab = [
            "model", "learns", "patterns", "data",
            "neural", "network", "training", "loss",
            "weights", "activation", "output", "input",
        ]
    words: list[DslWord] = []
    t = 0.0
    word_dur = 1.0 / words_per_second
    i = 0
    while t < duration_s:
        w = vocab[i % len(vocab)]
        words.append(DslWord(w=w, start=round(t, 3), end=round(t + word_dur * 0.8, 3), conf=0.99))
        t += word_dur
        i += 1
    return WordsSidecar(schema_version="1", words=words)


class TestSearchRelevant:
    def test_returns_ranges_matching_context(self):
        words = _make_dense_words(duration_s=60.0)
        ranges = search_relevant(words, "neural network training", target_duration_s=10.0)
        assert len(ranges) >= 1
        total = sum(r.end_s - r.start_s for r in ranges)
        assert total > 0

    def test_respects_target_duration(self):
        words = _make_dense_words(duration_s=120.0)
        ranges = search_relevant(words, "model training", target_duration_s=15.0)
        assert len(ranges) >= 1
        total = sum(r.end_s - r.start_s for r in ranges)
        assert 5.0 <= total <= 30.0

    def test_excludes_specified_ranges(self):
        words = _make_dense_words(duration_s=60.0)
        exclude = [(0.0, 30.0)]
        ranges = search_relevant(words, "model training", target_duration_s=10.0, exclude_ranges=exclude)
        for r in ranges:
            assert r.start_s >= 30.0

    def test_returns_empty_for_no_words(self):
        words = WordsSidecar(
            schema_version="1",
            segments=[{"text": "fallback", "start_s": 0.0, "end_s": 1.0}],
        )
        ranges = search_relevant(words, "anything", target_duration_s=5.0)
        assert ranges == []

    def test_returns_empty_when_all_excluded(self):
        words = _make_dense_words(duration_s=30.0)
        exclude = [(0.0, 60.0)]
        ranges = search_relevant(words, "model", target_duration_s=10.0, exclude_ranges=exclude)
        assert ranges == []

    def test_returns_empty_for_zero_duration(self):
        words = _make_dense_words(duration_s=30.0)
        ranges = search_relevant(words, "model", target_duration_s=0.0)
        assert ranges == []

    def test_range_text_contains_words(self):
        words = _make_dense_words(duration_s=30.0)
        ranges = search_relevant(words, "model patterns", target_duration_s=5.0)
        assert len(ranges) == 1
        assert len(ranges[0].text) > 0
        assert ranges[0].start_s < ranges[0].end_s


class TestFindCandidates:
    def test_writes_candidate_files(self, tmp_path):
        words = _make_dense_words(duration_s=120.0)
        output_dir = tmp_path / "relevant"
        candidates = find_candidates(words, "neural model", 15.0, 3, output_dir)
        assert len(candidates) >= 1
        for c in candidates:
            assert (output_dir / f"{c.stem}.json").exists()

    def test_candidates_are_non_overlapping(self, tmp_path):
        words = _make_dense_words(duration_s=120.0)
        output_dir = tmp_path / "relevant"
        candidates = find_candidates(words, "neural model", 10.0, 3, output_dir)
        all_ranges = [(r.start_s, r.end_s) for c in candidates for r in c.ranges]
        for i, (s1, e1) in enumerate(all_ranges):
            for j, (s2, e2) in enumerate(all_ranges):
                if i != j:
                    assert s1 >= e2 or e1 <= s2, f"overlap: ({s1},{e1}) and ({s2},{e2})"

    def test_respects_count_limit(self, tmp_path):
        words = _make_dense_words(duration_s=120.0)
        output_dir = tmp_path / "relevant"
        candidates = find_candidates(words, "neural model", 10.0, 5, output_dir)
        assert len(candidates) <= 5

    def test_stems_are_sequential(self, tmp_path):
        words = _make_dense_words(duration_s=120.0)
        output_dir = tmp_path / "relevant"
        candidates = find_candidates(words, "neural model", 10.0, 3, output_dir)
        for i, c in enumerate(candidates):
            assert c.stem == f"rel_{i + 1:02d}"


class TestLoadCandidate:
    def test_loads_existing_candidate(self, tmp_path):
        candidate = RelevantCandidate(
            stem="rel_01",
            ranges=[RelevantRange(start_s=10.0, end_s=20.0, text="some text")],
            total_duration_s=10.0,
        )
        path = tmp_path / "rel_01.json"
        path.write_text(candidate.model_dump_json(indent=2))

        loaded = load_candidate(tmp_path, "rel_01")
        assert loaded is not None
        assert loaded.stem == "rel_01"
        assert len(loaded.ranges) == 1
        assert loaded.ranges[0].start_s == 10.0

    def test_returns_none_for_missing(self, tmp_path):
        result = load_candidate(tmp_path, "nonexistent")
        assert result is None

    def test_roundtrip_through_find_and_load(self, tmp_path):
        words = _make_dense_words(duration_s=60.0)
        output_dir = tmp_path / "relevant"
        candidates = find_candidates(words, "model training", 10.0, 2, output_dir)
        assert len(candidates) >= 1

        loaded = load_candidate(output_dir, candidates[0].stem)
        assert loaded is not None
        assert loaded.stem == candidates[0].stem
        assert loaded.ranges == candidates[0].ranges
