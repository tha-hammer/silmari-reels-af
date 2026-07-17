"""B1 — the caption-path aligner anchors to the segment's ``timecode_s``.

Regression guard for ``bd ate`` (AF-9ja): when a phrase repeats across several
time-separated cues, distinct composite segments must resolve to the cue nearest
*their own* timecode, not collapse onto one global-best cue.
"""

from hypothesis import given
from hypothesis import strategies as st

from reel_af.dsl.aligner import FALLBACK_TIMECODE_WINDOW_S, align
from reel_af.dsl.models import FallbackSegment, WordsSidecar

PHRASE = "you know what I mean"


def _dup_sidecar() -> WordsSidecar:
    # same phrase in three time-separated cues + distractors (monotonic by start_s)
    return WordsSidecar(
        words=[],
        segments=[
            FallbackSegment(text="intro clip one", start_s=400.0, end_s=406.0),
            FallbackSegment(text=PHRASE, start_s=412.0, end_s=415.0),  # cue 1
            FallbackSegment(text="middle filler", start_s=428.0, end_s=431.0),
            FallbackSegment(text=PHRASE, start_s=432.0, end_s=435.0),  # cue 3
            FallbackSegment(text="later bridge", start_s=448.0, end_s=451.0),
            FallbackSegment(text=PHRASE, start_s=452.0, end_s=455.0),  # cue 5
        ],
    )


def test_duplicate_phrase_resolves_to_nearest_cue_by_timecode():
    side = _dup_sidecar()
    r1 = align(PHRASE, side, timecode_s=413.0)
    r2 = align(PHRASE, side, timecode_s=433.0)
    r3 = align(PHRASE, side, timecode_s=453.0)
    for r in (r1, r2, r3):
        assert r.kind == "aligned" and r.method == "cue_fallback"
    ranges = {r1.fallback_segment_range, r2.fallback_segment_range, r3.fallback_segment_range}
    assert ranges == {(1, 1), (3, 3), (5, 5)}  # distinct — no collapse
    assert (r1.start_s, r2.start_s, r3.start_s) == (412.0, 432.0, 452.0)


def test_timecode_none_keeps_global_argmax():
    # backward-compat: without an anchor, first best-scoring cue wins (old behavior)
    side = _dup_sidecar()
    r = align(PHRASE, side)  # timecode_s=None
    assert r.kind == "aligned" and r.fallback_segment_range == (1, 1)


@given(k=st.integers(min_value=0, max_value=5))
def test_window_separated_duplicates_are_injective(k: int):
    # K identical-text cues whose spans are separated by > FALLBACK_TIMECODE_WINDOW_S:
    # anchoring at cue k's midpoint must resolve to exactly cue k (injective).
    gap = FALLBACK_TIMECODE_WINDOW_S * 3  # spans separated by >> the window
    segments = [
        FallbackSegment(text=PHRASE, start_s=100.0 + i * gap, end_s=102.0 + i * gap)
        for i in range(6)
    ]
    side = WordsSidecar(words=[], segments=segments)
    midpoint = (segments[k].start_s + segments[k].end_s) / 2
    r = align(PHRASE, side, timecode_s=midpoint)
    assert r.kind == "aligned"
    assert r.fallback_segment_range == (k, k)
