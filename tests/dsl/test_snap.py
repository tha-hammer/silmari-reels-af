from __future__ import annotations

from reel_af.dsl.models import DslWord, FallbackSegment, WordsSidecar
from reel_af.dsl.snap import (
    cue_boundaries,
    sentence_boundaries,
    snap_edge,
    snap_extend_edge,
    word_boundaries,
)


def _word(text: str, start: float, end: float) -> DslWord:
    return DslWord(w=text, start=start, end=end)


def test_boundary_extractors_preserve_sentence_word_and_cue_boundaries():
    words = [
        _word("They", 4.12, 4.30),
        _word("reason.", 4.55, 5.01),
        _word("Right.", 8.24, 8.62),
    ]
    cues = [
        FallbackSegment(text="They reason.", start_s=4.12, end_s=5.01),
        FallbackSegment(text="Right.", start_s=8.24, end_s=8.62),
    ]

    assert sentence_boundaries(words) == [5.01, 8.62]
    assert word_boundaries(words) == [4.12, 4.30, 4.55, 5.01, 8.24, 8.62]
    assert cue_boundaries(cues) == [4.12, 5.01, 8.24, 8.62]


def test_snap_edge_uses_nearest_boundary_within_tolerance_and_clamps():
    assert snap_edge(8.4, [7.9, 8.62], tol=1.0, clamp=(0.0, 9.0)) == 8.62
    assert snap_edge(8.4, [6.9], tol=1.0, clamp=(0.0, 9.0)) == 8.4
    assert snap_edge(12.0, [13.0], tol=1.0, clamp=(0.0, 10.0)) == 10.0


def test_extend_tail_prefers_sentence_boundary_then_respects_next_segment():
    sidecar = WordsSidecar(
        words=[
            _word("reasoning.", 7.26, 7.90),
            _word("Right.", 8.24, 8.62),
        ]
    )

    snapped = snap_extend_edge(
        "tail",
        start_s=4.12,
        end_s=7.90,
        duration_s=0.5,
        sidecar=sidecar,
        clamp_min=0.0,
        clamp_max=9.0,
    )

    assert snapped == 8.62

    clamped = snap_extend_edge(
        "tail",
        start_s=4.12,
        end_s=7.90,
        duration_s=0.5,
        sidecar=sidecar,
        next_start_s=8.1,
        clamp_min=0.0,
        clamp_max=9.0,
    )

    assert clamped == 8.1


def test_extend_head_clamps_at_zero_and_previous_segment():
    sidecar = WordsSidecar(words=[_word("Opening.", 0.0, 0.2), _word("They", 0.4, 0.6)])

    assert (
        snap_extend_edge(
            "head",
            start_s=0.4,
            end_s=1.0,
            duration_s=1.0,
            sidecar=sidecar,
            clamp_min=0.0,
        )
        == 0.0
    )

    assert (
        snap_extend_edge(
            "head",
            start_s=6.0,
            end_s=7.0,
            duration_s=2.0,
            sidecar=sidecar,
            previous_end_s=5.25,
            clamp_min=0.0,
        )
        == 5.25
    )


def test_extend_uses_cue_boundaries_when_words_are_unavailable():
    sidecar = WordsSidecar(
        words=[],
        segments=[
            FallbackSegment(text="first cue", start_s=3.0, end_s=5.0),
            FallbackSegment(text="second cue", start_s=8.0, end_s=10.0),
        ],
    )

    snapped = snap_extend_edge(
        "tail",
        start_s=3.0,
        end_s=5.0,
        duration_s=2.4,
        sidecar=sidecar,
        clamp_min=0.0,
        clamp_max=12.0,
    )

    assert snapped == 8.0
