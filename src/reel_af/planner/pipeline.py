"""Pipeline orchestration for the A1 planner producer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from reel_af.dsl.models import Diagnostic, WordsSidecar
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.lint import LintDiagnostic, lint_blueprint
from reel_af.planner.llm import BamlPlannerLLM, PlannerLLM
from reel_af.planner.models import DurationBounds, Register
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
    effective_bounds = _effective_bounds(bounds, cfg)
    transcript = _transcript_text(words)

    mined_candidates = await llm.mine(transcript, register)
    candidates, candidate_rejections = enforce_verbatim(
        mined_candidates,
        words,
        floor=cfg.verbatim_floor,
    )
    if not candidates:
        return {
            "error": PLANNER_EMPTY_CANDIDATE_SET,
            "diagnostics": [_verbatim_diag_dict(rejection) for rejection in candidate_rejections],
        }

    strategy = await llm.strategize(transcript, candidates, effective_bounds)

    attempts = cfg.max_repair_passes + 1
    last_unresolved: list[Any] = []
    repair_hint: str | None = None
    for _attempt in range(attempts):
        blueprint = await llm.arrange(candidates, strategy, repair_hint=repair_hint)
        resolved = resolve_timecodes(blueprint.beats, words)
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
        )
        if any(diag.severity == "error" for diag in lint_diags):
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
            duration_bounds_s=effective_bounds,
        )
        return _write_triple(out_dir, composite, words, hook_plan)

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
) -> dict[str, str]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    composite_path = root / "composite.ts.md"
    words_path = root / "transcript.words.json"
    hook_path = root / "hook-plan.json"

    composite_path.write_text(composite, encoding="utf-8")
    words_path.write_text(
        json.dumps(words.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    hook_path.write_text(
        json.dumps(hook_plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "composite_ref": str(composite_path),
        "words_ref": str(words_path),
        "hook_ref": str(hook_path),
    }


def _transcript_text(words: WordsSidecar) -> str:
    if words.segments:
        return " ".join(segment.text for segment in words.segments)
    return " ".join(word.w for word in words.words)


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
