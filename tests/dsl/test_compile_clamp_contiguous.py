"""AF-e1x — segments tile contiguously in source time (no seam replay).

The aligner returns each segment's full caption cue, whose end_s can overrun the
next segment's start_s. Rendered, that re-speaks the overlap at every seam. The
compile stage clamps end_s to the next start_s so each source moment plays once.
"""

from pathlib import Path

from reel_af.dsl.compile import (
    _AlignedSegment,
    _normalize_source_intervals,
    compile_composite,
    load_words,
)
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import Diagnostic, SourceRef, SourceSegment

FIX = Path(__file__).resolve().parent / "fixtures"


class _Seg:
    def __init__(self, index):
        self.index, self.source, self.timecode_s = index, None, 0.0


def _aligned(spans):
    return [_AlignedSegment(_Seg(i), s, e, "t") for i, (s, e) in enumerate(spans)]


def test_overrun_clamped_to_next_start():
    a = _aligned([(412.0, 440.0), (432.0, 458.0), (452.0, 460.0)])
    assert _normalize_source_intervals(a, "fixture", []) is False
    assert [(x.start_s, x.end_s) for x in a] == [
        (412.0, 432.0),  # was 440; clamped to next start 432
        (432.0, 452.0),  # was 458; clamped to next start 452
        (452.0, 460.0),  # last segment keeps its cue end
    ]


def test_non_overlapping_spans_unchanged():
    a = _aligned([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)])
    assert _normalize_source_intervals(a, "fixture", []) is False
    assert [(x.start_s, x.end_s) for x in a] == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]


def test_clamp_never_inverts_a_span():
    # next starts before cur ends but after cur starts; clamp to a positive-length span
    a = _aligned([(10.0, 30.0), (20.0, 25.0)])
    assert _normalize_source_intervals(a, "fixture", []) is False
    assert [(x.start_s, x.end_s) for x in a] == [(10.0, 20.0), (20.0, 25.0)]
    assert all(x.end_s > x.start_s for x in a)


def test_reordered_overrun_clamped_by_source_time_not_composite_order():
    diagnostics: list[Diagnostic] = []
    a = _aligned([(20.0, 28.0), (10.0, 18.0), (18.0, 23.0)])

    assert _normalize_source_intervals(a, "fixture", diagnostics) is False

    assert [(x.start_s, x.end_s) for x in a] == [
        (20.0, 28.0),
        (10.0, 18.0),
        (18.0, 20.0),
    ]
    assert diagnostics == []
    sorted_spans = sorted((x.start_s, x.end_s) for x in a)
    for (_, left_end), (right_start, _) in zip(sorted_spans, sorted_spans[1:]):
        assert left_end <= right_start


def test_compile_tiles_overlapping_cues_contiguously():
    doc = read_composite(
        (FIX / "seam_overlap.ts.md").read_text(), source_path=FIX / "seam_overlap.ts.md"
    )
    words = load_words(FIX / "seam_overlap.words.json")
    res = compile_composite(doc, words, SourceRef(source_url="https://example.com/s.mp4"))
    assert res.status in ("ok", "warning") and res.plan is not None
    spans = [(s.start_s, s.end_s) for s in res.plan.segments if isinstance(s, SourceSegment)]
    assert len(spans) == 3
    # contiguous tiling: each segment ends exactly where the next begins — no overlap
    for (_, e0), (s1, _) in zip(spans, spans[1:]):
        assert e0 == s1
    # total covered source time equals the outer span (each moment counted once)
    assert spans[-1][1] - spans[0][0] == sum(e - s for s, e in spans)
