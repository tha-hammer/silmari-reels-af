"""Pipeline orchestration for the A1 planner producer."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from reel_af.dsl.models import Diagnostic, WordsSidecar
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.lint import LintDiagnostic, lint_blueprint
from reel_af.planner.llm import BamlPlannerLLM, PlannerLLM
from reel_af.planner.models import (
    CandidateSpan,
    DurationBounds,
    DurationPolicy,
    PlannerCandidate,
    Register,
)
from reel_af.planner.serialize import (
    PlannedCutIn,
    build_hook_plan,
    resolve_timecodes,
    serialize_composite,
)
from reel_af.planner.verbatim import VerbatimRejection, enforce_verbatim

PLANNER_EMPTY_CANDIDATE_SET = "planner_empty_candidate_set"
PLANNER_UNMATCHED_SEGMENT = "planner_unmatched_segment"
PLANNER_RETENTION_LINT_FAILED = "retention_lint_failed"


@dataclass(frozen=True)
class TranscriptWindow:
    """A deterministic source-time transcript window for candidate mining."""

    window_id: str
    index: int
    start_s: float
    end_s: float
    text: str


async def plan(
    source_url: str,
    words: WordsSidecar,
    register: Register = "educational",
    bounds: Mapping[str, float] | DurationBounds | None = None,
    *,
    llm: PlannerLLM | None = None,
    out_dir: str | Path,
    cfg: PlannerConfig | None = None,
) -> dict[str, Any]:
    """Produce `{composite.ts.md, transcript.words.json, hook-plan.json}` refs.

    The producer stops at data delivery. Rendering remains owned by
    `reel_dsl_hooks_to_reels`, which is why this function writes artifacts and
    returns refs rather than stitching media.
    """

    cfg = cfg or load_planner_config()
    llm = llm or BamlPlannerLLM(cfg=cfg)
    duration_policy = _duration_policy(bounds, cfg)
    transcript = _transcript_text(words)

    mined_candidates: list[CandidateSpan] = []
    for window in _transcript_windows(words, cfg):
        window_candidates = await llm.mine(window.text, register)
        window_candidates = _limit_candidate_spans(
            window_candidates,
            limit=cfg.mine_candidates_per_window,
        )
        mined_candidates.extend(_with_window_metadata(window_candidates, window))

    candidates, candidate_rejections = enforce_verbatim(
        mined_candidates,
        words,
        floor=cfg.verbatim_floor,
    )
    candidates = _cap_candidates_with_source_diversity(candidates, cfg)
    if not candidates:
        return {
            "error": PLANNER_EMPTY_CANDIDATE_SET,
            "diagnostics": [_verbatim_diag_dict(rejection) for rejection in candidate_rejections],
        }

    strategy = await llm.strategize(transcript, candidates, duration_policy)

    attempts = cfg.max_repair_passes + 1
    last_unresolved: list[Any] = []
    repair_hint: str | None = None
    for _attempt in range(attempts):
        blueprint = await llm.arrange(candidates, strategy, repair_hint=repair_hint)
        resolved = resolve_timecodes(
            blueprint.beats,
            words,
            candidates=candidates,
            floor=cfg.verbatim_floor,
        )
        last_unresolved = [item for item in resolved if not item.resolved]
        if last_unresolved:
            repair_hint = _repair_hint(
                last_unresolved,
                candidates,
                words,
                max_chars=cfg.max_repair_hint_chars,
            )
            continue

        lint_diags = lint_blueprint(
            blueprint,
            words=words,
            cfg=cfg,
            resolved=resolved,
            register=register,
            duration_policy=duration_policy,
            strategy=strategy,
            candidates=candidates,
        )
        lint_errors = [diag for diag in lint_diags if diag.severity == "error"]
        if lint_errors:
            r7_errors = [diag for diag in lint_errors if diag.rule == "R7"]
            if r7_errors and _attempt < attempts - 1:
                repair_hint = _r7_repair_hint(r7_errors, max_chars=cfg.max_repair_hint_chars)
                continue
            return {
                "error": PLANNER_RETENTION_LINT_FAILED,
                "diagnostics": [_lint_diag_dict(diag) for diag in lint_diags],
            }

        composite = serialize_composite(blueprint, resolved)
        composite_ref = str(Path(out_dir) / "composite.ts.md")
        hook_plan = build_hook_plan(
            source_url=source_url,
            hook=blueprint.hook,
            span=resolved,
            cut_ins=_cut_ins(blueprint, resolved),
            composite_ref=composite_ref,
            model=cfg.model,
            duration_bounds_s=_policy_duration_bounds(duration_policy),
        )
        return _write_triple(
            out_dir,
            composite,
            words,
            hook_plan,
            mined_candidates=mined_candidates,
            accepted_candidates=candidates,
            strategy=strategy,
            blueprint=blueprint,
        )

    return {
        "error": PLANNER_UNMATCHED_SEGMENT,
        "diagnostics": [
            _diagnostic_dict(
                Diagnostic(
                    code="UNMATCHED_SEGMENT",
                    message=f"could not align beat {item.index}: {item.span_quote!r}",
                    severity="error",
                    context={
                        "beat_index": item.index,
                        "reason": item.reason,
                        "best_quality": item.quality,
                    },
                )
            )
            for item in last_unresolved
        ],
    }


def _write_triple(
    out_dir: str | Path,
    composite: str,
    words: WordsSidecar,
    hook_plan: Mapping[str, Any],
    *,
    mined_candidates: Any | None = None,
    accepted_candidates: Any | None = None,
    strategy: Any | None = None,
    blueprint: Any | None = None,
) -> dict[str, str]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    composite_path = root / "composite.ts.md"
    words_path = root / "transcript.words.json"
    hook_path = root / "hook-plan.json"
    mined_candidates_path = root / "mined-candidates.json"
    accepted_candidates_path = root / "accepted-candidates.json"
    strategy_path = root / "strategy.json"
    blueprint_path = root / "blueprint.json"

    composite_path.write_text(composite, encoding="utf-8")
    _write_json(words_path, words)
    _write_json(hook_path, hook_plan)
    _write_json(mined_candidates_path, mined_candidates or [])
    _write_json(accepted_candidates_path, accepted_candidates or [])
    _write_json(strategy_path, strategy)
    _write_json(blueprint_path, blueprint)

    return {
        "composite_ref": str(composite_path),
        "words_ref": str(words_path),
        "hook_ref": str(hook_path),
        "mined_candidates_ref": str(mined_candidates_path),
        "accepted_candidates_ref": str(accepted_candidates_path),
        "strategy_ref": str(strategy_path),
        "blueprint_ref": str(blueprint_path),
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _transcript_text(words: WordsSidecar) -> str:
    if words.segments:
        return " ".join(segment.text for segment in words.segments)
    return " ".join(word.w for word in words.words)


def _transcript_windows(words: WordsSidecar, cfg: PlannerConfig) -> list[TranscriptWindow]:
    full_text = _transcript_text(words)
    duration_s = _source_duration_s(words)
    if duration_s <= cfg.mine_window_duration_s:
        return [TranscriptWindow("w000", 0, 0.0, max(0.0, duration_s), full_text)]

    window_s, starts = _window_schedule(
        duration_s=duration_s,
        window_s=cfg.mine_window_duration_s,
        overlap_s=cfg.mine_window_overlap_s,
        max_windows=cfg.mine_max_windows,
    )
    windows: list[TranscriptWindow] = []
    for index, start_s in enumerate(starts):
        end_s = min(duration_s, start_s + window_s)
        text = _window_text(words, start_s=start_s, end_s=end_s) or full_text
        windows.append(
            TranscriptWindow(
                window_id=f"w{index:03d}",
                index=index,
                start_s=float(start_s),
                end_s=float(end_s),
                text=text,
            )
        )
    return windows


def _window_schedule(
    *,
    duration_s: float,
    window_s: float,
    overlap_s: float,
    max_windows: int,
) -> tuple[float, list[float]]:
    if max_windows <= 1:
        return duration_s, [0.0]
    step_s = max(1.0, window_s - overlap_s)
    estimated = int(math.ceil(max(0.0, duration_s - window_s) / step_s)) + 1
    if estimated <= max_windows:
        starts: list[float] = []
        start_s = 0.0
        while start_s < duration_s:
            starts.append(start_s)
            if start_s + window_s >= duration_s:
                break
            start_s += step_s
        return window_s, starts

    count = max_windows
    adaptive_window_s = min(duration_s, (duration_s / count) + overlap_s)
    last_start = max(0.0, duration_s - adaptive_window_s)
    if count == 1:
        return adaptive_window_s, [0.0]
    starts = [last_start * index / (count - 1) for index in range(count)]
    return adaptive_window_s, starts


def _source_duration_s(words: WordsSidecar) -> float:
    word_end = max((float(word.end) for word in words.words), default=0.0)
    segment_end = max((float(segment.end_s) for segment in words.segments), default=0.0)
    return max(word_end, segment_end)


def _window_text(words: WordsSidecar, *, start_s: float, end_s: float) -> str:
    if words.segments:
        pieces = [
            segment.text.strip()
            for segment in words.segments
            if float(segment.end_s) > start_s and float(segment.start_s) < end_s
        ]
        return " ".join(piece for piece in pieces if piece).strip()
    pieces = [
        word.w.strip()
        for word in words.words
        if float(word.end) > start_s and float(word.start) < end_s
    ]
    return " ".join(piece for piece in pieces if piece).strip()


def _limit_candidate_spans(candidates: Sequence[CandidateSpan], *, limit: int) -> list[CandidateSpan]:
    if len(candidates) <= limit:
        return list(candidates)
    return sorted(candidates, key=lambda candidate: _score(candidate), reverse=True)[:limit]


def _with_window_metadata(
    candidates: Sequence[CandidateSpan],
    window: TranscriptWindow,
) -> list[CandidateSpan]:
    update = {
        "source_window_id": window.window_id,
        "source_window_index": window.index,
        "source_window_start_s": window.start_s,
        "source_window_end_s": window.end_s,
    }
    annotated: list[CandidateSpan] = []
    for candidate in candidates:
        if hasattr(candidate, "model_copy"):
            annotated.append(candidate.model_copy(update=update))
            continue
        payload = _jsonable(candidate)
        if isinstance(payload, Mapping):
            annotated.append(CandidateSpan.model_validate({**payload, **update}))
            continue
        annotated.append(candidate)
    return annotated


def _cap_candidates_with_source_diversity(
    candidates: Sequence[PlannerCandidate],
    cfg: PlannerConfig,
) -> list[PlannerCandidate]:
    if len(candidates) <= cfg.max_candidates:
        return list(candidates)

    selected: list[PlannerCandidate] = []
    selected_ids: set[str] = set()
    groups: dict[int, list[PlannerCandidate]] = {}
    for candidate in candidates:
        window_index = _candidate_window_index(candidate)
        groups.setdefault(window_index, []).append(candidate)

    for group in groups.values():
        group.sort(key=lambda candidate: _score(candidate), reverse=True)

    for window_index in sorted(groups):
        for candidate in groups[window_index][: cfg.mine_candidates_per_window]:
            if len(selected) >= cfg.max_candidates:
                return selected
            selected.append(candidate)
            selected_ids.add(_candidate_identity(candidate))

    remaining = [
        candidate
        for candidate in candidates
        if _candidate_identity(candidate) not in selected_ids
    ]
    remaining.sort(key=lambda candidate: _score(candidate), reverse=True)
    for candidate in remaining:
        if len(selected) >= cfg.max_candidates:
            break
        selected.append(candidate)
    return selected


def _candidate_window_index(candidate: Any) -> int:
    value = _get(candidate, "source_window_index", None)
    if value is None:
        return -1
    return int(value)


def _candidate_identity(candidate: Any) -> str:
    return f"{_get(candidate, 'candidate_id', '')}:{_get(candidate, 'occurrence_index', '')}"


def _score(candidate: Any) -> float:
    try:
        return float(_get(candidate, "value_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cut_ins(blueprint: Any, resolved: list[Any]) -> list[PlannedCutIn]:
    cut_ins: list[PlannedCutIn] = []
    for beat, item in zip(getattr(blueprint, "beats", []), resolved, strict=False):
        cut_in = getattr(beat, "cutin", None)
        if cut_in is None or item.start_s is None or item.end_s is None:
            continue
        cut_ins.append(
            PlannedCutIn(
                cut_in=cut_in,
                beat_start_s=float(item.start_s),
                beat_end_s=float(item.end_s),
            )
        )
    return cut_ins


def _effective_bounds(
    bounds: Mapping[str, float] | DurationBounds | None,
    cfg: PlannerConfig,
) -> DurationBounds:
    if bounds is None:
        return cfg.bounds_default
    if isinstance(bounds, DurationBounds):
        return bounds
    return DurationBounds(
        min_s=float(bounds.get("min_s", bounds.get("min"))),
        max_s=float(bounds.get("max_s", bounds.get("max"))),
    )


def _duration_policy(
    bounds: Mapping[str, float] | DurationBounds | None,
    cfg: PlannerConfig,
) -> DurationPolicy:
    requested = _effective_bounds(bounds, cfg)
    soft_cap_s = float(cfg.r7_soft_cap_s)
    requested_max_s = float(requested.max_s)
    cap_overridden = requested_max_s > soft_cap_s
    return DurationPolicy(
        soft_cap_s=soft_cap_s,
        effective_cap_s=requested_max_s if cap_overridden else soft_cap_s,
        advisory_min_s=float(requested.min_s),
        advisory_max_s=requested_max_s,
        cap_overridden=cap_overridden,
    )


def _policy_duration_bounds(policy: DurationPolicy) -> DurationBounds:
    return DurationBounds(
        min_s=float(_get(policy, "advisory_min_s", 0.0) or 0.0),
        max_s=float(_get(policy, "effective_cap_s", _get(policy, "soft_cap_s", 180.0))),
    )


def _lint_diag_dict(diag: LintDiagnostic) -> dict[str, Any]:
    return diag.model_dump(exclude_none=True)


def _diagnostic_dict(diag: Diagnostic) -> dict[str, Any]:
    return diag.model_dump(mode="json", exclude_none=True)


def _verbatim_diag_dict(rejection: VerbatimRejection) -> dict[str, Any]:
    alignment = rejection.alignment
    return _diagnostic_dict(
        Diagnostic(
            code="CANDIDATE_NOT_FOUND",
            message=f"candidate {rejection.candidate_id} failed verbatim alignment",
            severity="error",
            context={
                "candidate_id": rejection.candidate_id,
                "quote": rejection.candidate.quote,
                "reason": rejection.reason,
                "best_quality": _alignment_quality(alignment),
                "nearby_words": rejection.nearby_words,
            },
        )
    )


def _alignment_quality(alignment: Any) -> float:
    return float(getattr(alignment, "quality", getattr(alignment, "best_quality", 0.0)))


def _repair_hint(
    unresolved: list[Any],
    candidates: list[Any],
    words: WordsSidecar,
    *,
    max_chars: int,
) -> str:
    lines = []
    for item in unresolved:
        candidate = _candidate_for_unresolved(item, candidates)
        candidate_id = _get(candidate, "candidate_id", f"beat[{item.index}]")
        reason = item.reason or "below_floor"
        line = f"candidate {candidate_id} {reason}: {item.span_quote!r}"
        nearby = _candidate_nearby_words(candidate, words)
        if nearby:
            line = f"{line} near {nearby!r}"
        lines.append(line)
    hint = "; ".join(lines)
    return hint[:max_chars]


def _r7_repair_hint(diagnostics: list[LintDiagnostic], *, max_chars: int) -> str:
    messages = "; ".join(diag.message for diag in diagnostics)
    hint = (
        f"{messages}. Make a coherent under-cap cut: drop optional support, repeated examples, "
        "and lower-value branches first; keep hook, minimum context, proof, payoff, and R8 loop."
    )
    return hint[:max_chars]


def _candidate_for_unresolved(item: Any, candidates: list[Any]) -> Any:
    beat_candidate_id = _get(getattr(item, "beat", None), "candidate_id", None)
    if beat_candidate_id:
        for candidate in candidates:
            if _get(candidate, "candidate_id", None) == beat_candidate_id:
                return candidate
    if 0 <= item.index < len(candidates):
        return candidates[item.index]
    return candidates[0] if candidates else None


def _candidate_nearby_words(candidate: Any, words: WordsSidecar) -> str | None:
    if candidate is None:
        return None
    word_range = _get(candidate, "word_range", None)
    if word_range is not None and words.words:
        start, end = int(word_range[0]), int(word_range[1])
        start = max(0, start)
        end = min(len(words.words) - 1, end)
        if start <= end:
            return " ".join(word.w.strip() for word in words.words[start : end + 1]).strip()
    quote = _get(candidate, "quote", None)
    return str(quote) if quote else None


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = [
    "PLANNER_EMPTY_CANDIDATE_SET",
    "PLANNER_RETENTION_LINT_FAILED",
    "PLANNER_UNMATCHED_SEGMENT",
    "plan",
]
