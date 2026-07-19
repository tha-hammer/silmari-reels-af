"""Post-mine verbatim enforcement for planner candidates."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from reel_af.dsl.aligner import align
from reel_af.dsl.models import (
    MATCH_QUALITY_FLOOR,
    AlignedSpan,
    AlignResult,
    UnmatchedSpan,
    WordsSidecar,
)
from reel_af.planner.models import CandidateSpan, PlannerCandidate, validate_candidate_span
from reel_af.planner.transcribe import word_range_to_aligned_span


class VerbatimRejection(BaseModel):
    """A mined candidate rejected by the aligner-backed verbatim gate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    candidate: CandidateSpan
    alignment: AlignResult
    reason: str
    nearby_words: str | None = None


@dataclass(frozen=True)
class _CandidateWindow:
    candidate_id: str
    occurrence_index: int
    start: int
    end: int


def enforce_verbatim(
    candidates: Sequence[CandidateSpan | Mapping[str, Any] | Any],
    words: WordsSidecar,
    *,
    floor: float = MATCH_QUALITY_FLOOR,
) -> tuple[list[PlannerCandidate], list[VerbatimRejection]]:
    """Return only mined candidates that align verbatim at or above ``floor``."""

    if floor < MATCH_QUALITY_FLOOR:
        raise ValueError(
            f"planner verbatim floor {floor} is below MATCH_QUALITY_FLOOR={MATCH_QUALITY_FLOOR}"
        )

    kept: list[PlannerCandidate] = []
    dropped: list[VerbatimRejection] = []
    occurrence_counts: defaultdict[str, int] = defaultdict(int)

    for index, raw_candidate in enumerate(candidates, start=1):
        candidate = _candidate_span(raw_candidate)
        candidate_id = _candidate_id(raw_candidate, index)
        result = align(candidate.quote, words)

        if not _accepted(result, floor):
            dropped.append(
                VerbatimRejection(
                    candidate_id=candidate_id,
                    candidate=candidate,
                    alignment=result,
                    reason=_reason(result, floor),
                    nearby_words=_nearby_words(words, candidate, result),
                )
            )
            continue

        quote_key = _quote_key(candidate.quote)
        occurrence_index = int(_raw_value(raw_candidate, "occurrence_index", occurrence_counts[quote_key]))
        occurrence_counts[quote_key] += 1
        kept.append(_planner_candidate(candidate, candidate_id, occurrence_index, result))

    return kept, dropped


def resolve_span_quote(
    beat: Mapping[str, Any] | Any,
    candidates: Sequence[PlannerCandidate | Mapping[str, Any] | Any],
    words: WordsSidecar,
    *,
    floor: float = MATCH_QUALITY_FLOOR,
) -> AlignedSpan | UnmatchedSpan:
    """Resolve a beat quote under the relaxed span-join + trim policy.

    A beat quote may be an exact normalized source-token substring of its
    referenced candidate, or a contiguous exact source-token span that starts in
    that candidate and extends through later adjacent candidate word ranges.
    For joins, the existing BAML `candidate_id`/`occurrence_index` pair names
    the first candidate in source-time order.
    """

    if floor < MATCH_QUALITY_FLOOR:
        raise ValueError(
            f"planner verbatim floor {floor} is below MATCH_QUALITY_FLOOR={MATCH_QUALITY_FLOOR}"
        )

    quote = str(_raw_value(beat, "span_quote", "")).strip()
    alignment = align(quote, words)
    if not _accepted(alignment, floor):
        return alignment

    candidate_id = _raw_value(beat, "candidate_id", None)
    occurrence_index = _coerce_occurrence(_raw_value(beat, "occurrence_index", None))
    if not candidate_id or occurrence_index is None:
        return _policy_rejection(quote, alignment)

    allowed_range = _allowed_word_range(
        quote=quote,
        words=words,
        candidates=candidates,
        candidate_id=str(candidate_id),
        occurrence_index=occurrence_index,
    )
    if allowed_range is None:
        return _policy_rejection(quote, alignment)

    return word_range_to_aligned_span(
        words,
        allowed_range,
        quality=float(getattr(alignment, "quality", 0.0)),
    )


def _accepted(result: AlignedSpan | UnmatchedSpan, floor: float) -> bool:
    return isinstance(result, AlignedSpan) and result.quality >= floor


def _planner_candidate(
    candidate: CandidateSpan,
    candidate_id: str,
    occurrence_index: int,
    result: AlignedSpan,
) -> PlannerCandidate:
    if result.word_range is None:
        raise ValueError("planner candidates require word-level transcript alignment")
    return PlannerCandidate(
        candidate_id=candidate_id,
        quote=candidate.quote,
        occurrence_index=occurrence_index,
        word_range=list(result.word_range),
        start_s=float(result.start_s),
        end_s=float(result.end_s),
        quality=float(result.quality),
        value_score=float(candidate.value_score),
        emotion=candidate.emotion or None,
        is_claim=candidate.is_claim,
        payoff_worthy=candidate.payoff_worthy,
        rationale=candidate.rationale or None,
    )


def _candidate_span(raw_candidate: CandidateSpan | Mapping[str, Any] | Any) -> CandidateSpan:
    if isinstance(raw_candidate, CandidateSpan):
        return validate_candidate_span(raw_candidate)
    if hasattr(raw_candidate, "model_dump"):
        return validate_candidate_span(CandidateSpan.model_validate(raw_candidate.model_dump()))
    if isinstance(raw_candidate, Mapping):
        return validate_candidate_span(CandidateSpan.model_validate(raw_candidate))
    return validate_candidate_span(
        CandidateSpan.model_validate(
            {
                "quote": _raw_value(raw_candidate, "quote"),
                "approx_start_s": _raw_value(raw_candidate, "approx_start_s"),
                "approx_end_s": _raw_value(raw_candidate, "approx_end_s"),
                "value_score": _raw_value(raw_candidate, "value_score"),
                "emotion": _raw_value(raw_candidate, "emotion", ""),
                "is_claim": _raw_value(raw_candidate, "is_claim", False),
                "payoff_worthy": _raw_value(raw_candidate, "payoff_worthy", False),
                "rationale": _raw_value(raw_candidate, "rationale", None),
            }
        )
    )


def _candidate_id(raw_candidate: CandidateSpan | Mapping[str, Any] | Any, index: int) -> str:
    value = _raw_value(raw_candidate, "candidate_id", None)
    if value:
        return str(value)
    return f"c{index:03d}"


def _reason(result: AlignedSpan | UnmatchedSpan, floor: float) -> str:
    if isinstance(result, UnmatchedSpan):
        return result.reason
    if result.quality < floor:
        return "below_floor"
    return "unmatched"


def _nearby_words(
    words: WordsSidecar,
    candidate: CandidateSpan,
    result: AlignedSpan | UnmatchedSpan,
) -> str | None:
    if not words.words:
        return None
    if isinstance(result, AlignedSpan) and result.word_range is not None:
        start, end = result.word_range
    else:
        start = _nearest_word_index(words, candidate.approx_start_s or 0.0)
        end = min(len(words.words) - 1, start + 5)
    return " ".join(word.w.strip() for word in words.words[start : end + 1]).strip() or None


def _nearest_word_index(words: WordsSidecar, target_s: float) -> int:
    return min(range(len(words.words)), key=lambda index: abs(words.words[index].start - target_s))


def _quote_key(quote: str) -> str:
    return " ".join(quote.casefold().split())


def _allowed_word_range(
    *,
    quote: str,
    words: WordsSidecar,
    candidates: Sequence[PlannerCandidate | Mapping[str, Any] | Any],
    candidate_id: str,
    occurrence_index: int,
) -> tuple[int, int] | None:
    windows = _candidate_windows(candidates, word_count=len(words.words))
    if not windows or not words.words:
        return None

    for word_range in _exact_source_word_ranges(quote, words):
        if _covered_by_candidate_policy(
            word_range,
            windows,
            candidate_id=candidate_id,
            occurrence_index=occurrence_index,
        ):
            return word_range
    return None


def _candidate_windows(
    candidates: Sequence[PlannerCandidate | Mapping[str, Any] | Any],
    *,
    word_count: int,
) -> list[_CandidateWindow]:
    windows: list[_CandidateWindow] = []
    for candidate in candidates:
        candidate_id = _raw_value(candidate, "candidate_id", None)
        occurrence_index = _coerce_occurrence(_raw_value(candidate, "occurrence_index", None))
        word_range = _raw_value(candidate, "word_range", None)
        if not candidate_id or occurrence_index is None or word_range is None:
            continue
        if len(word_range) != 2:
            continue
        start = int(word_range[0])
        end = int(word_range[1])
        if start < 0 or end < start or end >= word_count:
            continue
        windows.append(
            _CandidateWindow(
                candidate_id=str(candidate_id),
                occurrence_index=occurrence_index,
                start=start,
                end=end,
            )
        )
    return sorted(windows, key=lambda item: (item.start, item.end, item.candidate_id))


def _exact_source_word_ranges(quote: str, words: WordsSidecar) -> list[tuple[int, int]]:
    query = _normalize_words(quote)
    if not query:
        return []

    source = [
        (index, normalized)
        for index, word in enumerate(words.words)
        if (normalized := _normalize_token(word.w))
    ]
    source_tokens = [normalized for _index, normalized in source]
    ranges: list[tuple[int, int]] = []
    qlen = len(query)
    for index in range(len(source_tokens) - qlen + 1):
        if source_tokens[index : index + qlen] == query:
            ranges.append((source[index][0], source[index + qlen - 1][0]))
    return ranges


def _covered_by_candidate_policy(
    word_range: tuple[int, int],
    windows: Sequence[_CandidateWindow],
    *,
    candidate_id: str,
    occurrence_index: int,
) -> bool:
    start, end = word_range
    first_windows = [
        window
        for window in windows
        if window.candidate_id == candidate_id
        and window.occurrence_index == occurrence_index
        and window.start <= start <= window.end
    ]
    return any(_candidate_chain_covers(first, end, windows) for first in first_windows)


def _candidate_chain_covers(
    first: _CandidateWindow,
    end: int,
    windows: Sequence[_CandidateWindow],
) -> bool:
    covered_end = first.end
    if end <= covered_end:
        return True

    while covered_end < end:
        next_window = _next_adjacent_window(windows, covered_end + 1)
        if next_window is None:
            return False
        covered_end = next_window.end

    return True


def _next_adjacent_window(
    windows: Sequence[_CandidateWindow],
    expected_start: int,
) -> _CandidateWindow | None:
    options = [window for window in windows if window.start == expected_start]
    if not options:
        return None
    return max(options, key=lambda item: item.end)


def _policy_rejection(
    quote: str,
    alignment: AlignedSpan | UnmatchedSpan,
) -> UnmatchedSpan:
    return UnmatchedSpan(
        normalized_text=" ".join(_normalize_words(quote)),
        best_quality=_alignment_quality(alignment),
        reason="below_floor",
    )


def _alignment_quality(alignment: AlignedSpan | UnmatchedSpan) -> float:
    quality = getattr(alignment, "quality", getattr(alignment, "best_quality", 0.0))
    return max(0.0, min(1.0, float(quality)))


def _normalize_token(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _normalize_words(text: str) -> list[str]:
    return _normalize_token(text).split()


def _coerce_occurrence(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _raw_value(raw: CandidateSpan | Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return getattr(raw, key, default)


__all__ = [
    "PlannerCandidate",
    "VerbatimRejection",
    "enforce_verbatim",
    "resolve_span_quote",
]
