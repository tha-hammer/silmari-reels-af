from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from reel_af.dsl.aligner import _longest_run, align
from reel_af.dsl.models import DslWord, WordsSidecar


def _word(text: str, start: float, end: float) -> DslWord:
    return DslWord(w=text, start=start, end=end)


def test_align_exact_contiguous_segment_to_word_span():
    words = [
        _word("They", 4.12, 4.30),
        _word("don't", 4.30, 4.55),
        _word("reason", 4.55, 5.01),
    ]

    span = align("They don't reason", WordsSidecar(words=words))

    assert span.kind == "aligned"
    assert span.start_s == 4.12
    assert span.end_s == 5.01
    assert span.quality == 1.0
    assert span.word_range == (0, 2)
    assert span.method == "exact"


def test_align_exact_normalizes_punctuation_and_case():
    words = [
        _word("Intro", 1.0, 1.2),
        _word("They", 4.12, 4.30),
        _word("DON'T", 4.30, 4.55),
        _word("reason.", 4.55, 5.01),
        _word("Outro", 5.20, 5.50),
    ]

    span = align("they don't reason", WordsSidecar(words=words))

    assert span.kind == "aligned"
    assert span.word_range == (1, 3)
    assert span.method == "exact"


def test_align_exact_duplicate_words_uses_nearest_timecode_anchor():
    words = [
        _word("make", 100.0, 100.2),
        _word("a", 100.2, 100.3),
        _word("script", 100.3, 100.6),
        _word("to", 100.6, 100.8),
        _word("do", 100.8, 101.0),
        _word("it", 101.0, 101.2),
        _word("filler", 200.0, 200.4),
        _word("make", 1611.171, 1611.3),
        _word("a", 1611.3, 1611.4),
        _word("script", 1611.4, 1611.7),
        _word("to", 1611.7, 1611.9),
        _word("do", 1611.9, 1612.05),
        _word("it.", 1612.05, 1612.17),
    ]

    span = align("make a script to do it.", WordsSidecar(words=words), timecode_s=1611.5)

    assert span.kind == "aligned"
    assert span.word_range == (7, 12)
    assert span.start_s == 1611.171
    assert span.method == "exact"


def test_longest_run_returns_longest_contiguous_exact_match():
    query = "they dont reason at scale".split()
    source = "intro they dont reason outro they dont reason at scale closer".split()

    assert _longest_run(query, source) == (5, 9)


@given(
    start=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    duration=st.floats(min_value=0.01, max_value=10, allow_nan=False, allow_infinity=False),
)
def test_exact_aligned_spans_have_positive_monotonic_times(start: float, duration: float):
    words = [
        _word("alpha", start, start + duration / 2),
        _word("beta", start + duration / 2, start + duration),
    ]

    span = align("alpha beta", WordsSidecar(words=words))

    assert span.kind == "aligned"
    assert 0 <= span.start_s < span.end_s
