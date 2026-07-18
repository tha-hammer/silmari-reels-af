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
from reel_af.planner.models import Register
from reel_af.planner.serialize import (
    build_hook_plan,
    resolve_timecodes,
    serialize_composite,
)

PLANNER_UNMATCHED_SEGMENT = "planner_unmatched_segment"
PLANNER_RETENTION_LINT_FAILED = "retention_lint_failed"


async def plan(
    source_url: str,
    words: WordsSidecar,
    register: Register = "educational",
    bounds: Mapping[str, float] | None = None,
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
    llm = llm or BamlPlannerLLM()
    transcript = _transcript_text(words)

    candidates = await llm.mine(transcript, register)
    strategy = await llm.strategize(transcript, candidates, bounds)

    attempts = cfg.max_repair_passes + 1
    last_unresolved: list[Any] = []
    for _attempt in range(attempts):
        blueprint = await llm.arrange(candidates, strategy)
        resolved = resolve_timecodes(blueprint.beats, words)
        last_unresolved = [item for item in resolved if not item.resolved]
        if last_unresolved:
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
            cut_ins=_cut_ins(blueprint),
            composite_ref=composite_ref,
            model=cfg.model,
            duration_bounds_s=bounds,
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


def _cut_ins(blueprint: Any) -> list[Any]:
    return [
        beat.cutin
        for beat in getattr(blueprint, "beats", [])
        if getattr(beat, "cutin", None) is not None
    ]


def _lint_diag_dict(diag: LintDiagnostic) -> dict[str, Any]:
    return diag.model_dump(exclude_none=True)


def _diagnostic_dict(diag: Diagnostic) -> dict[str, Any]:
    return diag.model_dump(mode="json", exclude_none=True)


__all__ = [
    "PLANNER_RETENTION_LINT_FAILED",
    "PLANNER_UNMATCHED_SEGMENT",
    "plan",
]
