from __future__ import annotations

from reel_af.dsl.aligner import _normalize_words, _trigram_cosine, align
from reel_af.dsl.models import (
    MATCH_QUALITY_FLOOR,
    DslWord,
    FallbackSegment,
    WordsSidecar,
)


def _words(text: str, *, start: float = 10.0, step: float = 0.25) -> list[DslWord]:
    out: list[DslWord] = []
    t = start
    for token in text.split():
        out.append(DslWord(w=token, start=t, end=t + step))
        t += step
    return out


def test_trigram_cosine_scores_identical_text_as_one():
    tokens = _normalize_words("And the moment you trust the feeling")

    assert _trigram_cosine(tokens, tokens) == 1.0


def test_fuzzy_aligns_longer_paraphrase_to_shorter_source_span():
    source = "And the moment you trust the feeling you ship the bug"
    query = "Honestly, and the exact moment you trust the feeling, you ship the bug today."
    sidecar = WordsSidecar(words=_words(source))

    span = align(query, sidecar)

    assert span.kind == "aligned"
    assert span.method == "fuzzy"
    assert span.quality >= MATCH_QUALITY_FLOOR
    assert span.word_range == (0, 10)
    assert span.start_s == 10.0
    assert span.end_s == 12.75


def test_repeated_fuzzy_phrase_tie_chooses_earliest_span():
    phrase = "A loop you can actually see closing"
    sidecar = WordsSidecar(words=_words(f"{phrase} pause {phrase}"))

    span = align("A loop you actually see closing", sidecar)

    assert span.kind == "aligned"
    assert span.method == "fuzzy"
    assert span.word_range == (0, 6)


def test_below_floor_returns_typed_unmatched_span():
    sidecar = WordsSidecar(words=_words("They do not reason about systems"))

    span = align("bananas orbit under glass", sidecar)

    assert span.kind == "unmatched"
    assert span.reason == "below_floor"
    assert span.normalized_text == "bananas orbit under glass"
    assert 0.0 <= span.best_quality < MATCH_QUALITY_FLOOR


def test_caption_only_sidecar_uses_cue_fallback():
    sidecar = WordsSidecar(
        words=[],
        segments=[
            FallbackSegment(
                text="A loop you can actually see closing.",
                start_s=79.05,
                end_s=81.16,
            )
        ],
    )

    span = align("A loop you actually see closing", sidecar)

    assert span.kind == "aligned"
    assert span.method == "cue_fallback"
    assert span.fallback_segment_range == (0, 0)
    assert span.start_s == 79.05
    assert span.end_s == 81.16


def test_empty_query_and_empty_source_return_unmatched_without_raising():
    word_only = WordsSidecar(words=_words("They do not reason"))

    empty_query = align(" ,,, ", word_only)

    assert empty_query.kind == "unmatched"
    assert empty_query.reason == "empty_query"

    empty_source = align(
        "They do not reason",
        WordsSidecar.model_construct(words=[], segments=[]),
    )

    assert empty_source.kind == "unmatched"
    assert empty_source.reason == "empty_source"
