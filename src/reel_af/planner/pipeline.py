"""Pipeline orchestration for the A1 planner producer."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from reel_af.dsl.compile import compile_composite
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    DSL_HOOKS_WORKFLOW,
    CompileContext,
    Diagnostic,
    SourceRef,
    WordsSidecar,
)
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
from reel_af.planner.script_coherence import (
    build_candidate_contexts,
    build_script_beats,
    build_script_transitions,
    coherence_diagnostics,
    coherence_repair_hint,
    contextual_candidate_pool,
    strategy_candidate_ids,
)
from reel_af.planner.serialize import (
    HookClipInput,
    PlannedCutIn,
    build_hook_plan,
    resolve_timecodes,
    serialize_composite,
)
from reel_af.planner.verbatim import VerbatimRejection, enforce_verbatim

PLANNER_EMPTY_CANDIDATE_SET = "planner_empty_candidate_set"
PLANNER_UNMATCHED_SEGMENT = "planner_unmatched_segment"
PLANNER_RETENTION_LINT_FAILED = "retention_lint_failed"
PLANNER_SCRIPT_COHERENCE_FAILED = "planner_script_coherence_failed"
PLANNER_RENDER_COMPILE_FAILED = "planner_render_compile_failed"
PLANNER_MULTI_CLIP_INSUFFICIENT_SPANS = "planner_multi_clip_insufficient_spans"
INVALID_CLIP_COUNT = "invalid_clip_count"
MAX_SCRIPT_COHERENCE_REPAIR_PASSES = 2


@dataclass(frozen=True)
class TranscriptWindow:
    """A deterministic source-time transcript window for candidate mining."""

    window_id: str
    index: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class _PlannedClip:
    idx: int
    composite: str
    composite_ref: str
    resolved: list[Any]
    cut_ins: list[PlannedCutIn]
    accepted_candidates: list[Any]
    strategy: Any
    blueprint: Any
    script_coherence: Any


async def plan(
    source_url: str,
    words: WordsSidecar,
    register: Register = "educational",
    bounds: Mapping[str, float] | DurationBounds | None = None,
    *,
    llm: PlannerLLM | None = None,
    out_dir: str | Path,
    cfg: PlannerConfig | None = None,
    clip_count: int = 1,
) -> dict[str, Any]:
    """Produce `{composite.ts.md, transcript.words.json, hook-plan.json}` refs.

    The producer stops at data delivery. Rendering remains owned by
    `reel_dsl_hooks_to_reels`, which is why this function writes artifacts and
    returns refs rather than stitching media.
    """

    try:
        clip_count = _validate_clip_count(clip_count)
    except ValueError as exc:
        return _invalid_clip_count_result(clip_count, str(exc))

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

    candidate_groups = _clip_candidate_groups(candidates, clip_count)
    if len(candidate_groups) != clip_count:
        return _multi_clip_insufficient_result(
            requested=clip_count,
            available=len(candidate_groups),
            reason="not enough non-overlapping candidate spans",
        )

    staged_clips: list[_PlannedClip] = []
    for idx, clip_candidates in enumerate(candidate_groups, start=1):
        clip_result = await _plan_one_clip(
            idx=idx,
            source_url=source_url,
            words=words,
            register=register,
            duration_policy=duration_policy,
            transcript=transcript,
            candidates=clip_candidates,
            llm=llm,
            cfg=cfg,
            out_dir=out_dir,
            composite_ref=_composite_ref_for_clip(out_dir, idx),
        )
        if isinstance(clip_result, Mapping):
            return dict(clip_result)
        staged_clips.append(clip_result)

    if not _planned_clips_non_overlapping(staged_clips):
        return _multi_clip_insufficient_result(
            requested=clip_count,
            available=len(staged_clips),
            reason="arranged clip spans overlap",
        )

    hook_plan = build_hook_plan(
        source_url=source_url,
        hook=staged_clips[0].blueprint.hook,
        clips=[
            HookClipInput(
                idx=clip.idx,
                hook=clip.blueprint.hook,
                span=clip.resolved,
                cut_ins=clip.cut_ins,
                composite_ref=clip.composite_ref,
            )
            for clip in staged_clips
        ],
        model=cfg.model,
        duration_bounds_s=_policy_duration_bounds(duration_policy),
    )
    return _write_planned_triple(
        out_dir,
        staged_clips,
        words,
        hook_plan,
        mined_candidates=mined_candidates,
    )


async def _plan_one_clip(
    *,
    idx: int,
    source_url: str,
    words: WordsSidecar,
    register: Register,
    duration_policy: DurationPolicy,
    transcript: str,
    candidates: list[Any],
    llm: PlannerLLM,
    cfg: PlannerConfig,
    out_dir: str | Path,
    composite_ref: str,
) -> _PlannedClip | dict[str, Any]:
    strategy = await llm.strategize(transcript, candidates, duration_policy)
    arrange_candidates = contextual_candidate_pool(
        candidates,
        words,
        selected_candidate_ids=strategy_candidate_ids(strategy),
    )
    candidate_contexts = build_candidate_contexts(arrange_candidates, words)

    general_repairs = 0
    coherence_repairs = 0
    max_coherence_repairs = (
        0 if cfg.max_repair_passes <= 0 else MAX_SCRIPT_COHERENCE_REPAIR_PASSES
    )
    attempts = cfg.max_repair_passes + max_coherence_repairs + 1
    last_unresolved: list[Any] = []
    repair_hint: str | None = None
    for _attempt in range(attempts):
        blueprint = await llm.arrange(
            arrange_candidates,
            strategy,
            candidate_contexts=candidate_contexts,
            repair_hint=repair_hint,
        )
        resolved = resolve_timecodes(
            blueprint.beats,
            words,
            candidates=arrange_candidates,
            floor=cfg.verbatim_floor,
        )
        last_unresolved = [item for item in resolved if not item.resolved]
        if last_unresolved:
            if general_repairs >= cfg.max_repair_passes:
                break
            general_repairs += 1
            repair_hint = _repair_hint(
                last_unresolved,
                arrange_candidates,
                words,
                max_chars=cfg.max_repair_hint_chars,
            )
            continue

        script_beats = build_script_beats(blueprint.beats, resolved)
        transitions = build_script_transitions(script_beats, resolved, words)
        script_coherence = await llm.check_script_coherence(
            blueprint,
            script_beats,
            transitions,
            strategy,
            candidate_contexts,
            repair_hint=repair_hint,
        )
        if not bool(_get(script_coherence, "coherent", False)):
            if coherence_repairs < max_coherence_repairs:
                coherence_repairs += 1
                repair_hint = coherence_repair_hint(
                    script_coherence,
                    max_chars=cfg.max_repair_hint_chars,
                )
                continue
            script_coherence_ref = _write_script_coherence(out_dir, script_coherence)
            return {
                "error": PLANNER_SCRIPT_COHERENCE_FAILED,
                "diagnostics": [
                    _diagnostic_dict(diag) for diag in coherence_diagnostics(script_coherence)
                ],
                "script_coherence_ref": script_coherence_ref,
            }

        lint_diags = lint_blueprint(
            blueprint,
            words=words,
            cfg=cfg,
            resolved=resolved,
            register=register,
            duration_policy=duration_policy,
            strategy=strategy,
            candidates=arrange_candidates,
        )
        lint_errors = [diag for diag in lint_diags if diag.severity == "error"]
        if lint_errors:
            r7_errors = [diag for diag in lint_errors if diag.rule == "R7"]
            r8_errors = [diag for diag in lint_errors if diag.rule == "R8"]
            if (r7_errors or r8_errors) and general_repairs < cfg.max_repair_passes:
                general_repairs += 1
                hints = []
                if r7_errors:
                    hints.append(_r7_repair_hint(r7_errors, max_chars=cfg.max_repair_hint_chars))
                if r8_errors:
                    # AF-9zs: the loop tie-back is mandatory — repair before failing.
                    hints.append(_r8_repair_hint(r8_errors, max_chars=cfg.max_repair_hint_chars))
                repair_hint = "; ".join(hints)[: cfg.max_repair_hint_chars]
                continue
            return {
                "error": PLANNER_RETENTION_LINT_FAILED,
                "diagnostics": [_lint_diag_dict(diag) for diag in lint_diags],
            }

        composite = serialize_composite(blueprint, resolved)
        compiled = _compile_render_composite(composite, words, source_url)
        if _get(compiled, "status", None) == "error" or _get(compiled, "plan", None) is None:
            if general_repairs < cfg.max_repair_passes:
                general_repairs += 1
                repair_hint = _render_compile_repair_hint(
                    _get(compiled, "diagnostics", []) or [],
                    max_chars=cfg.max_repair_hint_chars,
                )
                continue
            return {
                "error": PLANNER_RENDER_COMPILE_FAILED,
                "diagnostics": [
                    _diagnostic_dict(diag) for diag in _get(compiled, "diagnostics", []) or []
                ],
            }
        return _PlannedClip(
            idx=idx,
            composite=composite,
            composite_ref=composite_ref,
            resolved=resolved,
            cut_ins=_cut_ins(blueprint, resolved),
            accepted_candidates=arrange_candidates,
            strategy=strategy,
            blueprint=blueprint,
            script_coherence=script_coherence,
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


def _validate_clip_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("clip_count must be an integer")
    if value < 1:
        raise ValueError("clip_count must be >= 1")
    return value


def _invalid_clip_count_result(value: Any, message: str) -> dict[str, Any]:
    return {
        "error": INVALID_CLIP_COUNT,
        "diagnostics": [
            {
                "code": "INVALID_CLIP_COUNT",
                "message": message,
                "severity": "error",
                "context": {"clip_count": _jsonable(value)},
            }
        ],
    }


def _multi_clip_insufficient_result(
    *,
    requested: int,
    available: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "error": PLANNER_MULTI_CLIP_INSUFFICIENT_SPANS,
        "diagnostics": [
            {
                "code": "MULTI_CLIP_INSUFFICIENT_SPANS",
                "message": reason,
                "severity": "error",
                "context": {"requested": requested, "available": available},
            }
        ],
    }


def _clip_candidate_groups(
    candidates: Sequence[Any],
    clip_count: int,
) -> list[list[Any]]:
    if clip_count == 1:
        return [list(candidates)]

    selected: list[Any] = []
    selected_bounds: list[tuple[float, float]] = []
    for candidate in sorted(candidates, key=_candidate_source_order):
        bounds = _candidate_source_bounds(candidate)
        if bounds is None:
            continue
        if any(_bounds_overlap(bounds, existing) for existing in selected_bounds):
            continue
        selected.append(candidate)
        selected_bounds.append(bounds)
        if len(selected) == clip_count:
            break
    return [[candidate] for candidate in selected]


def _candidate_source_order(candidate: Any) -> tuple[float, float, float, str]:
    bounds = _candidate_source_bounds(candidate)
    if bounds is None:
        return (math.inf, math.inf, -_score(candidate), _candidate_identity(candidate))
    return (bounds[0], bounds[1], -_score(candidate), _candidate_identity(candidate))


def _candidate_source_bounds(candidate: Any) -> tuple[float, float] | None:
    start = _get(candidate, "start_s", _get(candidate, "approx_start_s", None))
    end = _get(candidate, "end_s", _get(candidate, "approx_end_s", None))
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return None
    if end_f <= start_f:
        return None
    return start_f, end_f


def _planned_clips_non_overlapping(clips: Sequence[_PlannedClip]) -> bool:
    bounds: list[tuple[float, float]] = []
    for clip in clips:
        clip_bounds = _resolved_source_bounds(clip.resolved)
        if clip_bounds is None:
            return False
        bounds.append(clip_bounds)
    ordered = sorted(bounds)
    return all(not _bounds_overlap(previous, current) for previous, current in zip(ordered, ordered[1:], strict=False))


def _resolved_source_bounds(resolved: Sequence[Any]) -> tuple[float, float] | None:
    starts: list[float] = []
    ends: list[float] = []
    for item in resolved:
        if not _get(item, "resolved", False):
            return None
        start = _get(item, "start_s", None)
        end = _get(item, "end_s", None)
        if start is None or end is None:
            return None
        starts.append(float(start))
        ends.append(float(end))
    if not starts or not ends:
        return None
    start = min(starts)
    end = max(ends)
    if end <= start:
        return None
    return start, end


def _bounds_overlap(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return left[1] > right[0] and right[1] > left[0]


def _composite_ref_for_clip(out_dir: str | Path, idx: int) -> str:
    root = Path(out_dir)
    if idx == 1:
        return str(root / "composite.ts.md")
    return str(root / "clips" / f"clip-{idx:03d}" / "composite.ts.md")


def _write_planned_triple(
    out_dir: str | Path,
    clips: Sequence[_PlannedClip],
    words: WordsSidecar,
    hook_plan: Mapping[str, Any],
    *,
    mined_candidates: Any | None = None,
) -> dict[str, Any]:
    if not clips:
        raise ValueError("at least one planned clip is required")
    if len(clips) == 1:
        result = _write_triple(
            out_dir,
            clips[0].composite,
            words,
            hook_plan,
            mined_candidates=mined_candidates,
            accepted_candidates=clips[0].accepted_candidates,
            strategy=clips[0].strategy,
            blueprint=clips[0].blueprint,
            script_coherence=clips[0].script_coherence,
        )
        result["clip_count"] = 1
        return result
    return _write_multi_clip_triple(
        out_dir,
        clips,
        words,
        hook_plan,
        mined_candidates=mined_candidates,
    )


def _write_multi_clip_triple(
    out_dir: str | Path,
    clips: Sequence[_PlannedClip],
    words: WordsSidecar,
    hook_plan: Mapping[str, Any],
    *,
    mined_candidates: Any | None = None,
) -> dict[str, Any]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    composite_path = root / "composite.ts.md"
    words_path = root / "transcript.words.json"
    hook_path = root / "hook-plan.json"
    mined_candidates_path = root / "mined-candidates.json"
    accepted_candidates_path = root / "accepted-candidates.json"
    strategy_path = root / "strategy.json"
    blueprint_path = root / "blueprint.json"
    script_coherence_path = root / "script-coherence.json"

    for clip in clips:
        clip_path = Path(clip.composite_ref)
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_text(clip.composite, encoding="utf-8")
        if clip.idx == 1 and clip_path != composite_path:
            composite_path.write_text(clip.composite, encoding="utf-8")

    if not composite_path.exists():
        first = clips[0]
        composite_path.write_text(first.composite, encoding="utf-8")

    _write_json(words_path, words)
    _write_json(hook_path, hook_plan)
    _write_json(mined_candidates_path, mined_candidates or [])
    _write_json(
        accepted_candidates_path,
        {
            "schema_version": "1",
            "clips": [
                {"idx": clip.idx, "accepted_candidates": clip.accepted_candidates}
                for clip in clips
            ],
        },
    )
    _write_json(
        strategy_path,
        {
            "schema_version": "1",
            "clips": [{"idx": clip.idx, "strategy": clip.strategy} for clip in clips],
        },
    )
    _write_json(
        blueprint_path,
        {
            "schema_version": "1",
            "clips": [{"idx": clip.idx, "blueprint": clip.blueprint} for clip in clips],
        },
    )
    _write_json(
        script_coherence_path,
        {
            "schema_version": "1",
            "clips": [
                {"idx": clip.idx, "script_coherence": clip.script_coherence}
                for clip in clips
            ],
        },
    )

    return {
        "composite_ref": str(composite_path),
        "words_ref": str(words_path),
        "hook_ref": str(hook_path),
        "mined_candidates_ref": str(mined_candidates_path),
        "accepted_candidates_ref": str(accepted_candidates_path),
        "strategy_ref": str(strategy_path),
        "blueprint_ref": str(blueprint_path),
        "script_coherence_ref": str(script_coherence_path),
        "clip_count": len(clips),
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
    script_coherence: Any | None = None,
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
    script_coherence_path = root / "script-coherence.json"

    composite_path.write_text(composite, encoding="utf-8")
    _write_json(words_path, words)
    _write_json(hook_path, hook_plan)
    _write_json(mined_candidates_path, mined_candidates or [])
    _write_json(accepted_candidates_path, accepted_candidates or [])
    _write_json(strategy_path, strategy)
    _write_json(blueprint_path, blueprint)
    _write_json(script_coherence_path, script_coherence)

    return {
        "composite_ref": str(composite_path),
        "words_ref": str(words_path),
        "hook_ref": str(hook_path),
        "mined_candidates_ref": str(mined_candidates_path),
        "accepted_candidates_ref": str(accepted_candidates_path),
        "strategy_ref": str(strategy_path),
        "blueprint_ref": str(blueprint_path),
        "script_coherence_ref": str(script_coherence_path),
    }


def _write_script_coherence(out_dir: str | Path, script_coherence: Any) -> str:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "script-coherence.json"
    _write_json(path, script_coherence)
    return str(path)


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


def _r8_repair_hint(diagnostics: list[LintDiagnostic], *, max_chars: int) -> str:
    """AF-9zs: the R8 loop tie-back is mandatory for EVERY strategy (including
    ProblemAgitateSolve) — steer the re-arrange toward a hook-echoing close."""
    messages = "; ".join(diag.message for diag in diagnostics)
    hint = (
        f"{messages}. The R8 loop tie-back is MANDATORY: make the final beat echo the key "
        "tokens of strategy.hook.span_quote using a source span DISTINCT from the hook beat, "
        "and set loop.final_span_quote/candidate_id/occurrence_index to that final beat."
    )
    return hint[:max_chars]


def _compile_render_composite(composite: str, words: WordsSidecar, source_url: str) -> Any:
    return compile_composite(
        read_composite(composite),
        words,
        SourceRef(source_url=source_url),
        context=CompileContext(
            workflow=DSL_HOOKS_WORKFLOW,
            source_url=source_url,
            delivery_required=True,
        ),
    )


def _render_compile_repair_hint(diagnostics: Sequence[Diagnostic], *, max_chars: int) -> str:
    rendered = "; ".join(_diagnostic_summary(diag) for diag in diagnostics)
    has_join_refusal = any(_get(diag, "code", None) == "JOIN_REFUSED" for diag in diagnostics)
    if has_join_refusal:
        hint = (
            "RENDER-COMPILE failed with JOIN_REFUSED. Use Trans/cut instead of Join unless "
            "the two adjacent spans are truly forward source-time neighbors after the real DSL "
            f"compiler aligns them. Diagnostics: {rendered}"
        )
    else:
        hint = f"RENDER-COMPILE failed. Repair the serialized script so it compiles. Diagnostics: {rendered}"
    return hint[:max_chars]


def _diagnostic_summary(diag: Diagnostic) -> str:
    context = _get(diag, "context", None) or {}
    context_text = ""
    if context:
        context_text = " " + json.dumps(_jsonable(context), sort_keys=True)
    return f"{_get(diag, 'code', 'UNKNOWN')}: {_get(diag, 'message', '')}{context_text}"


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
    "PLANNER_RENDER_COMPILE_FAILED",
    "PLANNER_SCRIPT_COHERENCE_FAILED",
    "PLANNER_UNMATCHED_SEGMENT",
    "plan",
]
