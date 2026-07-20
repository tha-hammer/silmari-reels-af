"""Deterministic script-coherence inputs and repair hints."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Any, Mapping

from reel_af.dsl.aligner import align
from reel_af.dsl.models import MATCH_QUALITY_FLOOR, Diagnostic, WordsSidecar
from reel_af.planner.models import (
    CandidateTranscriptContext,
    PlannerCandidate,
    ReelStrategy,
    ScriptBeatText,
    ScriptCoherenceFixAction,
    ScriptCoherenceReport,
    ScriptTransition,
    ScriptTransitionVerdict,
)

CONTEXT_WORDS = 6
MAX_CONNECTIVE_WORDS = 40


def strategy_candidate_ids(strategy: ReelStrategy) -> set[str]:
    """Return candidate ids named by the strategy's local arc."""

    ids: set[str] = set()
    hook_id = _get(_get(strategy, "hook", None), "candidate_id", None)
    if hook_id:
        ids.add(str(hook_id))
    loop_id = _get(_get(strategy, "loop", None), "candidate_id", None)
    if loop_id:
        ids.add(str(loop_id))
    arc = _get(strategy, "arc", None)
    for field in ("required_candidate_ids", "optional_candidate_ids"):
        for candidate_id in _get(arc, field, None) or []:
            if str(candidate_id).strip():
                ids.add(str(candidate_id).strip())
    return ids


def contextual_candidate_pool(
    candidates: Sequence[PlannerCandidate],
    words: WordsSidecar,
    *,
    selected_candidate_ids: Iterable[str] | None = None,
    context_words: int = CONTEXT_WORDS,
) -> list[PlannerCandidate]:
    """Add bounded before/after bridge candidates around selected source spans."""

    selected = {str(candidate_id) for candidate_id in selected_candidate_ids or []}
    include_all = not selected
    pool = list(candidates)
    seen = {_candidate_key(candidate) for candidate in pool}
    for candidate in candidates:
        candidate_id = str(_get(candidate, "candidate_id", ""))
        if candidate_id.startswith("ctx_"):
            continue
        if not include_all and candidate_id not in selected:
            continue
        word_range = _normalized_word_range(_get(candidate, "word_range", None), words)
        if word_range is None:
            continue
        start, end = word_range
        before = (max(0, start - context_words), start - 1)
        after = (end + 1, min(len(words.words) - 1, end + context_words))
        pool.extend(
            bridge
            for bridge in (
                _bridge_candidate(candidate, words, before, suffix="before"),
                _bridge_candidate(candidate, words, after, suffix="after"),
            )
            if bridge is not None and _candidate_key(bridge) not in seen
        )
        seen = {_candidate_key(item) for item in pool}
    return pool


def build_candidate_contexts(
    candidates: Sequence[PlannerCandidate],
    words: WordsSidecar,
    *,
    context_words: int = CONTEXT_WORDS,
) -> list[CandidateTranscriptContext]:
    """Summarize source-neighborhood context for arrange and coherence prompts."""

    ordered = sorted(
        enumerate(candidates),
        key=lambda item: (float(_get(item[1], "start_s", 0.0)), item[0]),
    )
    neighbor_by_key: dict[tuple[str, int], tuple[Any | None, Any | None]] = {}
    for index, (_original_index, candidate) in enumerate(ordered):
        prev_candidate = ordered[index - 1][1] if index > 0 else None
        next_candidate = ordered[index + 1][1] if index + 1 < len(ordered) else None
        neighbor_by_key[_candidate_key(candidate)] = (prev_candidate, next_candidate)

    contexts: list[CandidateTranscriptContext] = []
    for candidate in candidates:
        word_range = _normalized_word_range(_get(candidate, "word_range", None), words)
        if word_range is None:
            before_text = None
            after_text = None
            source_neighborhood = str(_get(candidate, "quote", "") or "").strip()
        else:
            start, end = word_range
            before_text = _words_text(words, max(0, start - context_words), start - 1)
            after_text = _words_text(words, end + 1, min(len(words.words) - 1, end + context_words))
            quote_text = _words_text(words, start, end) or str(_get(candidate, "quote", "") or "")
            source_neighborhood = " ".join(
                piece for piece in (before_text, quote_text, after_text) if piece
            ).strip()

        prev_candidate, next_candidate = neighbor_by_key.get(_candidate_key(candidate), (None, None))
        contexts.append(
            CandidateTranscriptContext(
                candidate_id=str(_get(candidate, "candidate_id", "")),
                occurrence_index=int(_get(candidate, "occurrence_index", 0) or 0),
                start_s=float(_get(candidate, "start_s", 0.0) or 0.0),
                end_s=float(_get(candidate, "end_s", 0.0) or 0.0),
                before_text=before_text or None,
                after_text=after_text or None,
                source_neighborhood=source_neighborhood,
                prev_candidate_id=_candidate_id(prev_candidate),
                next_candidate_id=_candidate_id(next_candidate),
                gap_to_prev_s=_gap(prev_candidate, candidate),
                gap_to_next_s=_gap(candidate, next_candidate),
            )
        )
    return contexts


def build_script_beats(beats: Sequence[Any], resolved: Sequence[Any]) -> list[ScriptBeatText]:
    """Build the exact resolved beat text reviewed by the coherence pass."""

    script_beats: list[ScriptBeatText] = []
    for index, (beat, item) in enumerate(zip(beats, resolved, strict=False)):
        script_beats.append(
            ScriptBeatText(
                index=index,
                role=_get(beat, "role"),
                candidate_id=str(_get(beat, "candidate_id", "")),
                occurrence_index=int(_get(beat, "occurrence_index", 0) or 0),
                span_quote=str(_get(item, "span_quote", _get(beat, "span_quote", ""))).strip(),
                start_s=_optional_float(_get(item, "start_s", None)),
                end_s=_optional_float(_get(item, "end_s", None)),
                rationale=_optional_str(_get(beat, "rationale", None)),
            )
        )
    return script_beats


def build_script_transitions(
    script_beats: Sequence[ScriptBeatText],
    resolved: Sequence[Any],
    words: WordsSidecar,
    *,
    max_connective_words: int = MAX_CONNECTIVE_WORDS,
) -> list[ScriptTransition]:
    """Build transition records including any short dropped source text between spans."""

    transitions: list[ScriptTransition] = []
    for index in range(max(0, len(script_beats) - 1)):
        from_beat = script_beats[index]
        to_beat = script_beats[index + 1]
        from_item = resolved[index]
        to_item = resolved[index + 1]
        transitions.append(
            ScriptTransition(
                index=index,
                from_beat_index=int(_get(from_beat, "index", index)),
                to_beat_index=int(_get(to_beat, "index", index + 1)),
                from_candidate_id=str(_get(from_beat, "candidate_id", "")),
                to_candidate_id=str(_get(to_beat, "candidate_id", "")),
                from_text=str(_get(from_beat, "span_quote", "")).strip(),
                to_text=str(_get(to_beat, "span_quote", "")).strip(),
                source_gap_s=_source_gap_s(from_item, to_item),
                connective_text=_connective_text(
                    from_item,
                    to_item,
                    words,
                    max_words=max_connective_words,
                ),
            )
        )
    return transitions


def coherence_repair_hint(report: ScriptCoherenceReport, *, max_chars: int) -> str:
    """Render a bounded arrange repair hint from a failed coherence report."""

    lines = [f"SCRIPT-COHERENCE failed: {_optional_str(_get(report, 'overall_rationale', ''))}"]
    for review in _get(report, "transitions", None) or []:
        verdict = _enum_name(_get(review, "verdict", ""))
        fix_action = _enum_name(_get(review, "fix_action", ""))
        if verdict == "Coherent" and fix_action == "Keep":
            continue
        pieces = [
            f"T{_get(review, 'transition_index', '?')}",
            f"beat {_get(review, 'from_beat_index', '?')}->{_get(review, 'to_beat_index', '?')}",
            f"verdict={verdict}",
            f"fix={fix_action}",
            f"why_present={bool(_get(review, 'why_present', False))}",
            f"rationale={_optional_str(_get(review, 'rationale', ''))}",
        ]
        missing_why = _optional_str(_get(review, "missing_why", None))
        if missing_why:
            pieces.append(f"missing_why={missing_why}")
        bridge_ids = [str(item) for item in _get(review, "suggested_bridge_candidate_ids", None) or []]
        if bridge_ids:
            pieces.append(f"bridge_candidates={','.join(bridge_ids)}")
        suggested_repair = _optional_str(_get(review, "suggested_repair", None))
        if suggested_repair:
            pieces.append(f"suggested_repair={suggested_repair}")
        lines.append(" ".join(pieces))
    report_hint = _optional_str(_get(report, "repair_hint", None))
    if report_hint:
        lines.append(f"model_repair_hint={report_hint}")
    return "; ".join(line for line in lines if line).strip()[:max_chars]


def coherence_diagnostics(report: ScriptCoherenceReport) -> list[Diagnostic]:
    """Return typed diagnostics for a coherence failure."""

    diagnostics: list[Diagnostic] = []
    for review in _get(report, "transitions", None) or []:
        if _enum_name(_get(review, "verdict", "")) == "Coherent":
            continue
        diagnostics.append(
            Diagnostic(
                code="SCRIPT_COHERENCE_FAILED",
                message=str(_get(review, "rationale", "")).strip()
                or "script transition failed coherence review",
                severity="error",
                context={
                    "transition_index": _get(review, "transition_index", None),
                    "from_beat_index": _get(review, "from_beat_index", None),
                    "to_beat_index": _get(review, "to_beat_index", None),
                    "verdict": _enum_wire(_get(review, "verdict", "")),
                    "fix_action": _enum_wire(_get(review, "fix_action", "")),
                    "why_present": _get(review, "why_present", None),
                    "missing_why": _get(review, "missing_why", None),
                    "suggested_bridge_candidate_ids": _get(
                        review,
                        "suggested_bridge_candidate_ids",
                        None,
                    ),
                    "suggested_repair": _get(review, "suggested_repair", None),
                },
            )
        )
    if diagnostics:
        return diagnostics
    return [
        Diagnostic(
            code="SCRIPT_COHERENCE_FAILED",
            message=str(_get(report, "overall_rationale", "")).strip()
            or "script failed coherence review",
            severity="error",
            context={"coherent": bool(_get(report, "coherent", False))},
        )
    ]


def _bridge_candidate(
    candidate: PlannerCandidate,
    words: WordsSidecar,
    word_range: tuple[int, int],
    *,
    suffix: str,
) -> PlannerCandidate | None:
    start, end = word_range
    if not words.words or start < 0 or end < start or start >= len(words.words):
        return None
    end = min(end, len(words.words) - 1)
    quote = _words_text(words, start, end)
    if not quote:
        return None
    expected_start_s, expected_end_s = _range_times(words, start, end)
    anchored = align(quote, words, timecode_s=(expected_start_s + expected_end_s) / 2)
    if (
        getattr(anchored, "kind", None) != "aligned"
        or float(getattr(anchored, "quality", 0.0)) < MATCH_QUALITY_FLOOR
        or getattr(anchored, "word_range", None) != (start, end)
    ):
        return None
    candidate_id = str(_get(candidate, "candidate_id", "candidate"))
    base_score = float(_get(candidate, "value_score", 0.5) or 0.5)
    return PlannerCandidate(
        candidate_id=f"ctx_{candidate_id}_{suffix}",
        quote=quote,
        occurrence_index=int(_get(candidate, "occurrence_index", 0) or 0),
        word_range=[int(anchored.word_range[0]), int(anchored.word_range[1])],
        start_s=float(anchored.start_s),
        end_s=float(anchored.end_s),
        source_window_id=_get(candidate, "source_window_id", None),
        source_window_index=_get(candidate, "source_window_index", None),
        source_window_start_s=_get(candidate, "source_window_start_s", None),
        source_window_end_s=_get(candidate, "source_window_end_s", None),
        quality=1.0,
        value_score=min(base_score, 0.55),
        emotion=_get(candidate, "emotion", None),
        is_claim=False,
        payoff_worthy=False,
        rationale=(
            f"local connective context {suffix} {candidate_id}; use only when needed to "
            "preserve the why between adjacent script lines"
        ),
    )


def _candidate_key(candidate: Any) -> tuple[str, int]:
    return (
        str(_get(candidate, "candidate_id", "")),
        int(_get(candidate, "occurrence_index", 0) or 0),
    )


def _candidate_id(candidate: Any | None) -> str | None:
    if candidate is None:
        return None
    candidate_id = str(_get(candidate, "candidate_id", "") or "").strip()
    return candidate_id or None


def _normalized_word_range(value: Any, words: WordsSidecar) -> tuple[int, int] | None:
    if value is None or not words.words:
        return None
    try:
        start = max(0, int(value[0]))
        end = min(len(words.words) - 1, int(value[1]))
    except (TypeError, ValueError, IndexError):
        return None
    if end < start:
        return None
    return start, end


def _words_text(words: WordsSidecar, start: int, end: int) -> str:
    if not words.words or end < start:
        return ""
    start = max(0, start)
    end = min(len(words.words) - 1, end)
    if end < start:
        return ""
    return " ".join(word.w.strip() for word in words.words[start : end + 1] if word.w.strip())


def _range_times(words: WordsSidecar, start: int, end: int) -> tuple[float, float]:
    start = max(0, min(start, len(words.words) - 1))
    end = max(start, min(end, len(words.words) - 1))
    return float(words.words[start].start), float(words.words[end].end)


def _gap(left: Any | None, right: Any | None) -> float | None:
    if left is None or right is None:
        return None
    left_end = _optional_float(_get(left, "end_s", None))
    right_start = _optional_float(_get(right, "start_s", None))
    if left_end is None or right_start is None:
        return None
    return max(0.0, right_start - left_end)


def _source_gap_s(from_item: Any, to_item: Any) -> float | None:
    return _gap(
        {"end_s": _get(from_item, "end_s", None)},
        {"start_s": _get(to_item, "start_s", None)},
    )


def _connective_text(
    from_item: Any,
    to_item: Any,
    words: WordsSidecar,
    *,
    max_words: int,
) -> str | None:
    from_range = _get(from_item, "word_range", None)
    to_range = _get(to_item, "word_range", None)
    if from_range is None or to_range is None or not words.words:
        return None
    try:
        start = int(from_range[1]) + 1
        end = int(to_range[0]) - 1
    except (TypeError, ValueError, IndexError):
        return None
    if end < start or end - start + 1 > max_words:
        return None
    text = _words_text(words, start, end)
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _enum_name(value: Any) -> str:
    if isinstance(value, Enum):
        return value.name
    text = str(value)
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _enum_wire(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = [
    "CONTEXT_WORDS",
    "MAX_CONNECTIVE_WORDS",
    "build_candidate_contexts",
    "build_script_beats",
    "build_script_transitions",
    "coherence_diagnostics",
    "coherence_repair_hint",
    "contextual_candidate_pool",
    "strategy_candidate_ids",
]
