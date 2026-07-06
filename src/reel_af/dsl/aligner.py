"""Composite Transcript DSL v2 — word-level alignment and snap helpers."""

from __future__ import annotations

import re
from collections import Counter

from reel_af.dsl.models import (
    MATCH_QUALITY_FLOOR,
    AlignedSpan,
    DslWord,
    FallbackSegment,
    UnmatchedSpan,
    WordsSidecar,
)
from reel_af.dsl.snap import sentence_boundaries, snap_edge, snap_extend_edge, word_boundaries


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _normalize_words(text: str) -> list[str]:
    return _normalize(text).split()


def _trigrams(words: list[str]) -> Counter[str]:
    text = " ".join(words)
    c: Counter[str] = Counter()
    for i in range(len(text) - 2):
        c[text[i : i + 3]] += 1
    return c


def _trigram_cosine(a: list[str], b: list[str]) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    dot = sum(ta[k] * tb[k] for k in ta if k in tb)
    mag_a = sum(v * v for v in ta.values()) ** 0.5
    mag_b = sum(v * v for v in tb.values()) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _longest_run(query: list[str], source: list[str]) -> tuple[int, int] | None:
    if not query or not source:
        return None

    best_start: int | None = None
    best_len = 0
    previous = [0] * (len(source) + 1)

    for q_word in query:
        current = [0] * (len(source) + 1)
        for s_idx, s_word in enumerate(source, start=1):
            if q_word != s_word:
                continue

            run_len = previous[s_idx - 1] + 1
            current[s_idx] = run_len
            run_start = s_idx - run_len

            if run_len > best_len or (
                run_len == best_len
                and best_start is not None
                and run_start < best_start
            ):
                best_start = run_start
                best_len = run_len
        previous = current

    if best_start is None:
        return None
    return (best_start, best_start + best_len - 1)


def align(
    text: str,
    words: WordsSidecar,
    *,
    source: object | None = None,
) -> AlignedSpan | UnmatchedSpan:
    query_norm = _normalize_words(text)
    if not query_norm:
        return UnmatchedSpan(
            normalized_text=_normalize(text),
            best_quality=0.0,
            reason="empty_query",
            source=source,
        )

    if words.words:
        result = _align_words(query_norm, words.words, source)
        if result is not None:
            return result

    if words.segments:
        result = _align_fallback(query_norm, text, words.segments, source)
        if result is not None:
            return result

    return UnmatchedSpan(
        normalized_text=" ".join(query_norm),
        best_quality=0.0,
        reason="empty_source" if not words.words and not words.segments else "below_floor",
        source=source,
    )


def _align_words(
    query_norm: list[str],
    word_list: list[DslWord],
    source: object | None,
) -> AlignedSpan | UnmatchedSpan | None:
    word_norms = [_normalize(w.w) for w in word_list]
    qlen = len(query_norm)

    exact = _find_exact_run(query_norm, word_norms)
    if exact is not None:
        start_idx, end_idx = exact
        return AlignedSpan(
            start_s=word_list[start_idx].start,
            end_s=word_list[end_idx].end,
            quality=1.0,
            word_range=(start_idx, end_idx),
            method="exact",
        )

    best_quality = 0.0
    best_span: tuple[int, int] | None = None

    max_window = min(len(word_norms), qlen + 2)
    for window in range(max_window, 0, -1):
        if window > len(word_norms):
            continue
        for i in range(len(word_norms) - window + 1):
            candidate = word_norms[i : i + window]
            q = _trigram_cosine(query_norm, candidate)
            candidate_span = (i, i + window - 1)
            if _is_better_fuzzy_span(q, candidate_span, best_quality, best_span):
                best_quality = q
                best_span = candidate_span

    if best_quality >= MATCH_QUALITY_FLOOR and best_span is not None:
        return AlignedSpan(
            start_s=word_list[best_span[0]].start,
            end_s=word_list[best_span[1]].end,
            quality=best_quality,
            word_range=best_span,
            method="fuzzy",
        )

    if best_quality > 0:
        return UnmatchedSpan(
            normalized_text=" ".join(query_norm),
            best_quality=best_quality,
            reason="below_floor",
            source=source,
        )

    return None


def _find_exact_run(query: list[str], words: list[str]) -> tuple[int, int] | None:
    run = _longest_run(query, words)
    if run is None:
        return None
    if run[1] - run[0] + 1 != len(query):
        return None
    return run


def _is_better_fuzzy_span(
    quality: float,
    span: tuple[int, int],
    best_quality: float,
    best_span: tuple[int, int] | None,
) -> bool:
    if quality > best_quality:
        return True
    if abs(quality - best_quality) > 1e-12:
        return False
    if best_span is None:
        return True

    span_len = span[1] - span[0] + 1
    best_len = best_span[1] - best_span[0] + 1
    if span_len != best_len:
        return span_len > best_len
    return span[0] < best_span[0]


def _align_fallback(
    query_norm: list[str],
    raw_text: str,
    segments: list[FallbackSegment],
    source: object | None,
) -> AlignedSpan | UnmatchedSpan | None:
    best_quality = 0.0
    best_idx: int | None = None

    for i, seg in enumerate(segments):
        seg_norm = _normalize_words(seg.text)
        if not seg_norm:
            continue
        q = _trigram_cosine(query_norm, seg_norm)
        if q > best_quality:
            best_quality = q
            best_idx = i

    if best_quality >= MATCH_QUALITY_FLOOR and best_idx is not None:
        seg = segments[best_idx]
        return AlignedSpan(
            start_s=seg.start_s,
            end_s=seg.end_s,
            quality=best_quality,
            fallback_segment_range=(best_idx, best_idx),
            method="cue_fallback",
        )

    return UnmatchedSpan(
        normalized_text=" ".join(query_norm),
        best_quality=best_quality,
        reason="below_floor",
        source=source,
    )


__all__ = [
    "align",
    "sentence_boundaries",
    "snap_edge",
    "snap_extend_edge",
    "word_boundaries",
]
