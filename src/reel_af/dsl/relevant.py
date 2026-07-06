"""Composite Transcript DSL v2 — relevant content search and candidate management.

Provides semantic-keyword search over a source word stream to find content
ranges that match surrounding context. Used by ``insert relevant``,
``find relevant``, and ``insert file`` markers.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from reel_af.dsl.models import DslWord, WordsSidecar

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "its", "as", "was", "are",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can",
    "this", "that", "these", "those", "i", "you", "he", "she", "we", "they",
    "not", "no", "so", "if", "then",
})

_WORD_RE = re.compile(r"[a-z]+(?:[-'][a-z]+)*")


class RelevantRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str


class RelevantCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stem: str
    ranges: list[RelevantRange] = Field(min_length=1)
    total_duration_s: float = Field(gt=0)


def _tokenize(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS]


def _word_excluded(word: DslWord, exclude_ranges: list[tuple[float, float]]) -> bool:
    for rs, re_ in exclude_ranges:
        if word.start >= rs and word.end <= re_:
            return True
    return False


def search_relevant(
    words: WordsSidecar,
    context_text: str,
    target_duration_s: float,
    exclude_ranges: list[tuple[float, float]] | None = None,
) -> list[RelevantRange]:
    """Find word-stream ranges relevant to context_text, totaling ~target_duration_s."""
    if not words.words or target_duration_s <= 0:
        return []
    if exclude_ranges is None:
        exclude_ranges = []

    context_terms = Counter(_tokenize(context_text))
    wlist = words.words

    best_score = -1.0
    best_i = -1
    best_j = -1

    for i in range(len(wlist)):
        if _word_excluded(wlist[i], exclude_ranges):
            continue

        j = i
        while j < len(wlist) and not _word_excluded(wlist[j], exclude_ranges):
            if wlist[j].end - wlist[i].start >= target_duration_s:
                break
            j += 1

        if j >= len(wlist) or _word_excluded(wlist[j], exclude_ranges):
            j -= 1

        if j < i:
            continue

        dur = wlist[j].end - wlist[i].start
        if dur <= 0:
            continue

        window_text = " ".join(w.w for w in wlist[i : j + 1])
        window_terms = Counter(_tokenize(window_text))

        if context_terms:
            overlap = sum((context_terms & window_terms).values())
        else:
            overlap = len(window_terms)

        dur_ratio = dur / target_duration_s
        if dur_ratio > 2.0:
            continue
        dur_bonus = 1.0 - abs(1.0 - dur_ratio) * 0.3
        score = overlap * max(dur_bonus, 0.1)

        if score > best_score:
            best_score = score
            best_i = i
            best_j = j

    if best_i < 0:
        return []

    return [
        RelevantRange(
            start_s=wlist[best_i].start,
            end_s=wlist[best_j].end,
            text=" ".join(w.w for w in wlist[best_i : best_j + 1]),
        )
    ]


def find_candidates(
    words: WordsSidecar,
    context_text: str,
    target_duration_s: float,
    count: int,
    output_dir: Path,
    exclude_ranges: list[tuple[float, float]] | None = None,
) -> list[RelevantCandidate]:
    """Write ``count`` candidate JSON files to ``output_dir`` and return them."""
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[RelevantCandidate] = []
    used: list[tuple[float, float]] = list(exclude_ranges) if exclude_ranges else []

    for idx in range(count):
        ranges = search_relevant(words, context_text, target_duration_s, used or None)
        if not ranges:
            break

        stem = f"rel_{idx + 1:02d}"
        total_dur = sum(r.end_s - r.start_s for r in ranges)
        candidate = RelevantCandidate(
            stem=stem,
            ranges=ranges,
            total_duration_s=total_dur,
        )

        path = output_dir / f"{stem}.json"
        path.write_text(candidate.model_dump_json(indent=2), encoding="utf-8")

        candidates.append(candidate)
        used.extend((r.start_s, r.end_s) for r in ranges)

    return candidates


def load_candidate(relevant_dir: Path, file_stem: str) -> RelevantCandidate | None:
    """Load a candidate from ``relevant_dir/file_stem.json``. Returns None if missing."""
    path = relevant_dir / f"{file_stem}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return RelevantCandidate.model_validate(data)


__all__ = [
    "RelevantCandidate",
    "RelevantRange",
    "find_candidates",
    "load_candidate",
    "search_relevant",
]
