"""Post-mine verbatim enforcement for planner candidates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
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


class VerbatimRejection(BaseModel):
    """A mined candidate rejected by the aligner-backed verbatim gate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    candidate: CandidateSpan
    alignment: AlignResult
    reason: str
    nearby_words: str | None = None


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


def _raw_value(raw: CandidateSpan | Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return getattr(raw, key, default)


__all__ = [
    "PlannerCandidate",
    "VerbatimRejection",
    "enforce_verbatim",
]
