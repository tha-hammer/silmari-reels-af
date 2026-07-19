"""Deterministic pre-gates for reel-quality judging."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from reel_af.dsl.aligner import align
from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    DSL_HOOKS_WORKFLOW,
    CompileContext,
    SourceRef,
    WordsSidecar,
)
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.lint import lint_blueprint
from reel_af.planner.serialize import resolve_timecodes, serialize_composite

from .models import BeatEvidence, BlueprintEvidence, GateCheck, PreGateResult


def evaluate_blueprint_pre_gates(
    blueprint: Any,
    words: WordsSidecar,
    *,
    source_url: str,
    case_id: str,
    register: str | None = None,
    cfg: PlannerConfig | None = None,
) -> tuple[PreGateResult, BlueprintEvidence, dict[str, str]]:
    """Run hard gates for a typed planner blueprint.

    The judge must not run unless all three checks pass:
    verbatim alignment, retention lint, and DSL compile status.
    """

    cfg = cfg or load_planner_config()
    resolved = resolve_timecodes(_get(blueprint, "beats", []), words)

    verbatim_diags = [
        {
            "beat_index": item.index,
            "quote": item.span_quote,
            "quality": item.quality,
            "reason": item.reason or "below_floor",
        }
        for item in resolved
        if not item.resolved or item.quality < cfg.verbatim_floor
    ]
    min_quality = min((float(item.quality) for item in resolved), default=0.0)
    verbatim_check = GateCheck(
        name="verbatim_align",
        passed=not verbatim_diags,
        score=min_quality,
        summary=f"min beat alignment quality {min_quality:.3f}; floor {cfg.verbatim_floor:.3f}",
        diagnostics=verbatim_diags,
    )

    lint_diags = lint_blueprint(
        blueprint,
        words=words,
        cfg=cfg,
        resolved=resolved,
        register=register or cfg.default_register,
    )
    lint_payload = [_dump_model(diag) for diag in lint_diags]
    lint_check = GateCheck(
        name="retention_lint",
        passed=not _has_error_diagnostic(lint_payload),
        summary=_lint_summary(lint_payload),
        diagnostics=lint_payload,
    )

    compile_check, _composite_text = _compile_blueprint_check(
        blueprint,
        resolved,
        words,
        source_url=source_url,
        verbatim_passed=verbatim_check.passed,
    )

    gates = PreGateResult(
        passed=all(check.passed for check in (verbatim_check, lint_check, compile_check)),
        checks=[verbatim_check, lint_check, compile_check],
    )
    evidence = _blueprint_evidence(
        blueprint,
        resolved,
        case_id=case_id,
        artifact_kind="blueprint",
        source_url=source_url,
        compile_status=compile_check.status,
        lint_diagnostics=lint_payload,
    )
    return gates, evidence, {}


def evaluate_artifact_triple(
    composite_path: str | Path,
    hook_plan_path: str | Path,
    words_path: str | Path,
    *,
    case_id: str,
    source_url: str | None = None,
    register: str | None = None,
    cfg: PlannerConfig | None = None,
) -> tuple[PreGateResult, BlueprintEvidence, dict[str, str]]:
    """Run gates for a persisted `{composite.ts.md, hook-plan.json, transcript.words.json}`."""

    cfg = cfg or load_planner_config()
    composite_path = Path(composite_path)
    hook_plan_path = Path(hook_plan_path)
    words_path = Path(words_path)
    composite_text = composite_path.read_text(encoding="utf-8")
    hook_plan = json.loads(hook_plan_path.read_text(encoding="utf-8"))
    words = load_words(words_path)
    artifact_dir = composite_path.parent
    blueprint_payload = _read_optional_json(artifact_dir / "blueprint.json")
    strategy_payload = _read_optional_json(artifact_dir / "strategy.json")
    mined_candidates = _read_optional_json(artifact_dir / "mined-candidates.json") or []
    accepted_candidates = _read_optional_json(artifact_dir / "accepted-candidates.json") or []
    source_url = source_url or str(hook_plan.get("source_url") or "")
    doc = read_composite(composite_text, source_path=composite_path)

    aligned = [align(segment.normalized_text, words) for segment in doc.segments]
    verbatim_diags = [
        {
            "segment_index": index,
            "quote": doc.segments[index].normalized_text,
            "quality": float(getattr(result, "quality", getattr(result, "best_quality", 0.0))),
            "reason": str(getattr(result, "reason", "below_floor")),
        }
        for index, result in enumerate(aligned)
        if getattr(result, "kind", None) != "aligned"
        or float(getattr(result, "quality", 0.0)) < cfg.verbatim_floor
    ]
    min_quality = min(
        (float(getattr(result, "quality", getattr(result, "best_quality", 0.0))) for result in aligned),
        default=0.0,
    )
    verbatim_check = GateCheck(
        name="verbatim_align",
        passed=not verbatim_diags,
        score=min_quality,
        summary=f"min segment alignment quality {min_quality:.3f}; floor {cfg.verbatim_floor:.3f}",
        diagnostics=verbatim_diags,
    )

    derived = _derived_blueprint_from_artifacts(doc, aligned, hook_plan)
    lint_source = blueprint_payload or derived
    lint_diags = lint_blueprint(
        lint_source,
        words=words,
        cfg=cfg,
        resolved=aligned,
        register=register or cfg.default_register,
    )
    lint_payload = [_dump_model(diag) for diag in lint_diags]
    lint_check = GateCheck(
        name="retention_lint",
        passed=not _has_error_diagnostic(lint_payload),
        summary=_lint_summary(lint_payload),
        diagnostics=lint_payload,
    )

    compile_check = _compile_artifact_check(doc, words, source_url=source_url)
    gates = PreGateResult(
        passed=all(check.passed for check in (verbatim_check, lint_check, compile_check)),
        checks=[verbatim_check, lint_check, compile_check],
    )
    evidence = _artifact_evidence(
        derived,
        aligned,
        hook_plan,
        case_id=case_id,
        source_url=source_url,
        compile_status=compile_check.status,
        lint_diagnostics=lint_payload,
        blueprint_payload=blueprint_payload,
        strategy_payload=strategy_payload,
        mined_candidates=mined_candidates,
        accepted_candidates=accepted_candidates,
    )
    refs = {
        "composite_ref": str(composite_path),
        "hook_ref": str(hook_plan_path),
        "words_ref": str(words_path),
    }
    for name, path in (
        ("blueprint_ref", artifact_dir / "blueprint.json"),
        ("strategy_ref", artifact_dir / "strategy.json"),
        ("mined_candidates_ref", artifact_dir / "mined-candidates.json"),
        ("accepted_candidates_ref", artifact_dir / "accepted-candidates.json"),
    ):
        if path.exists():
            refs[name] = str(path)
    return gates, evidence, refs


def _compile_blueprint_check(
    blueprint: Any,
    resolved: Sequence[Any],
    words: WordsSidecar,
    *,
    source_url: str,
    verbatim_passed: bool,
) -> tuple[GateCheck, str | None]:
    if not verbatim_passed:
        return (
            GateCheck(
                name="compile",
                passed=False,
                status="not_run",
                summary="compile skipped because verbatim alignment failed",
            ),
            None,
        )

    try:
        composite_text = serialize_composite(blueprint, resolved)
        doc = read_composite(composite_text)
        compiled = compile_composite(
            doc,
            words,
            SourceRef(source_url=source_url),
            context=CompileContext(workflow=DSL_HOOKS_WORKFLOW, source_url=source_url),
        )
    except Exception as exc:
        return (
            GateCheck(
                name="compile",
                passed=False,
                status="error",
                summary="compile raised before returning a result",
                diagnostics=[{"error": type(exc).__name__, "message": str(exc)}],
            ),
            None,
        )

    diagnostics = [_dump_model(diag) for diag in compiled.diagnostics]
    return (
        GateCheck(
            name="compile",
            passed=compiled.status == "ok",
            status=compiled.status,
            summary=f"compile status={compiled.status}",
            diagnostics=diagnostics,
        ),
        composite_text,
    )


def _compile_artifact_check(doc: Any, words: WordsSidecar, *, source_url: str) -> GateCheck:
    try:
        compiled = compile_composite(
            doc,
            words,
            SourceRef(source_url=source_url),
            context=CompileContext(workflow=DSL_HOOKS_WORKFLOW, source_url=source_url),
        )
    except Exception as exc:
        return GateCheck(
            name="compile",
            passed=False,
            status="error",
            summary="compile raised before returning a result",
            diagnostics=[{"error": type(exc).__name__, "message": str(exc)}],
        )

    diagnostics = [_dump_model(diag) for diag in compiled.diagnostics]
    return GateCheck(
        name="compile",
        passed=compiled.status == "ok",
        status=compiled.status,
        summary=f"compile status={compiled.status}",
        diagnostics=diagnostics,
    )


def _blueprint_evidence(
    blueprint: Any,
    resolved: Sequence[Any],
    *,
    case_id: str,
    artifact_kind: str,
    source_url: str,
    compile_status: str | None,
    lint_diagnostics: list[dict[str, Any]],
) -> BlueprintEvidence:
    beats = list(_get(blueprint, "beats", []))
    return BlueprintEvidence(
        case_id=case_id,
        artifact_kind=artifact_kind,
        source_url=source_url,
        template=_wire(_get(blueprint, "template_", None)),
        target_duration_s=_optional_float(_get(blueprint, "target_duration_s", None)),
        hook_banner=_optional_str(_get(_get(blueprint, "hook", {}), "banner_line", None)),
        hook_span_quote=_optional_str(_get(_get(blueprint, "hook", {}), "span_quote", None)),
        loop_final_span_quote=_optional_str(
            _get(_get(blueprint, "loop", {}), "final_span_quote", None)
        ),
        engagement_primary=_wire(_get(blueprint, "engagement_primary", None)),
        cta=_dump_value(_get(blueprint, "cta", {})),
        beats=[
            _beat_evidence(beat, resolved[index] if index < len(resolved) else None, index=index)
            for index, beat in enumerate(beats)
        ],
        engagement_lines=_engagement_lines(beats),
        cut_ins=_cut_ins_from_beats(beats),
        planner_rationale=_planner_rationale(blueprint=blueprint),
        compile_status=compile_status,
        lint_diagnostics=lint_diagnostics,
    )


def _artifact_evidence(
    derived: Mapping[str, Any],
    aligned: Sequence[Any],
    hook_plan: Mapping[str, Any],
    *,
    case_id: str,
    source_url: str,
    compile_status: str | None,
    lint_diagnostics: list[dict[str, Any]],
    blueprint_payload: Mapping[str, Any] | None = None,
    strategy_payload: Mapping[str, Any] | None = None,
    mined_candidates: Sequence[Any] | None = None,
    accepted_candidates: Sequence[Any] | None = None,
) -> BlueprintEvidence:
    clips = list(hook_plan.get("clips") or [])
    clip = clips[0] if clips else {}
    source = blueprint_payload or derived
    beats = list(_get(source, "beats", []) or [])
    cut_ins = _cut_ins_from_beats(beats)
    if not cut_ins:
        cut_ins = [_dump_value(item) for item in clip.get("cut_ins") or []]
    notes = []
    if blueprint_payload is None:
        notes.append(
            "Evidence was derived from the persisted production triple because BASELINE-0 "
            "does not include the raw BAML ReelBlueprint."
        )
    else:
        notes.append("Evidence included persisted blueprint.json sidecar.")
    if strategy_payload is not None:
        notes.append("Evidence included persisted strategy.json sidecar.")
    return BlueprintEvidence(
        case_id=case_id,
        artifact_kind="triple",
        source_url=source_url,
        template=_wire(_get(source, "template_", _get(source, "template", "derived_from_composite"))),
        target_duration_s=_optional_float(_get(source, "target_duration_s", None)),
        hook_banner=_optional_str(_get(_get(source, "hook", {}), "banner_line", None)),
        hook_span_quote=_optional_str(_get(_get(source, "hook", {}), "span_quote", None)),
        loop_final_span_quote=_optional_str(
            _get(_get(source, "loop", {}), "final_span_quote", None)
        ),
        engagement_primary=_wire(_get(source, "engagement_primary", None)),
        cta=_dump_value(_get(source, "cta", {})),
        beats=[
            _artifact_beat_evidence(
                beat,
                aligned[index] if index < len(aligned) else None,
                index=index,
            )
            for index, beat in enumerate(beats)
        ],
        engagement_lines=_engagement_lines(beats),
        cut_ins=cut_ins,
        planner_rationale=_planner_rationale(
            blueprint=source,
            strategy=strategy_payload,
            mined_candidates=mined_candidates,
            accepted_candidates=accepted_candidates,
        ),
        compile_status=compile_status,
        lint_diagnostics=lint_diagnostics,
        notes=notes,
    )


def _derived_blueprint_from_artifacts(
    doc: Any,
    aligned: Sequence[Any],
    hook_plan: Mapping[str, Any],
) -> dict[str, Any]:
    clips = list(hook_plan.get("clips") or [])
    clip = clips[0] if clips else {}
    segments = list(doc.segments)
    beats: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        result = aligned[index] if index < len(aligned) else None
        start_s = _optional_float(getattr(result, "start_s", None))
        end_s = _optional_float(getattr(result, "end_s", None))
        beat: dict[str, Any] = {
            "index": index,
            "role": _role_for_position(index, len(segments)),
            "span_quote": segment.normalized_text,
            "start_s": start_s,
            "end_s": end_s,
            "duration_s": (end_s - start_s) if start_s is not None and end_s is not None else None,
        }
        if segment.trailing_markers:
            beat["interrupt_out"] = {
                "kind": _wire(getattr(segment.trailing_markers[-1].marker, "kind", "marker"))
            }
        beats.append(beat)

    final_quote = segments[-1].normalized_text if segments else ""
    return {
        "template": "derived_from_composite",
        "target_duration_s": _target_duration(beats),
        "hook": {
            "banner_line": clip.get("hook") or clip.get("title") or "",
            "span_quote": clip.get("excerpt") or (segments[0].normalized_text if segments else ""),
        },
        "beats": beats,
        "loop": {"final_span_quote": final_quote},
        "engagement_primary": "unknown",
        "cta": {"placements": []},
    }


def _beat_evidence(beat: Any, resolved: Any | None, *, index: int) -> BeatEvidence:
    start_s = _optional_float(_get(resolved, "start_s", None))
    end_s = _optional_float(_get(resolved, "end_s", None))
    return BeatEvidence(
        index=index,
        role=_wire(_get(beat, "role", None)),
        span_quote=str(_get(beat, "span_quote", "")),
        start_s=start_s,
        end_s=end_s,
        duration_s=(end_s - start_s) if start_s is not None and end_s is not None else None,
        alignment_quality=_optional_float(_get(resolved, "quality", None)),
        interrupt_out=_wire(_get(_get(beat, "interrupt_out", None), "kind", None)),
        cutin=_dump_value(_get(beat, "cutin", None)) if _get(beat, "cutin", None) is not None else None,
        engagement=(
            _dump_value(_get(beat, "engagement", None))
            if _get(beat, "engagement", None) is not None
            else None
        ),
    )


def _artifact_beat_evidence(
    beat: Mapping[str, Any],
    aligned: Any | None,
    *,
    index: int,
) -> BeatEvidence:
    cutin = _get(beat, "cutin", _get(beat, "cut_in", None))
    engagement = _get(beat, "engagement", None)
    return BeatEvidence(
        index=int(_get(beat, "index", index)),
        role=_optional_str(_get(beat, "role", None)),
        span_quote=str(_get(beat, "span_quote", "")),
        start_s=_optional_float(_get(beat, "start_s", None)),
        end_s=_optional_float(_get(beat, "end_s", None)),
        duration_s=_optional_float(_get(beat, "duration_s", None)),
        alignment_quality=_optional_float(
            getattr(aligned, "quality", getattr(aligned, "best_quality", None))
        ),
        interrupt_out=_wire(_get(_get(beat, "interrupt_out", None), "kind", None)),
        cutin=_dump_value(cutin) if cutin is not None else None,
        engagement=_dump_value(engagement) if engagement is not None else None,
    )


def _role_for_position(index: int, count: int) -> str:
    if index == 0:
        return "hook"
    if count > 1 and index == count - 1:
        return "payoff"
    if index == 1:
        return "context"
    return "value"


def _target_duration(beats: Sequence[Mapping[str, Any]]) -> float | None:
    durations = [beat.get("duration_s") for beat in beats if beat.get("duration_s") is not None]
    if not durations:
        return None
    return float(sum(durations))


def _read_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _has_error_diagnostic(diagnostics: Sequence[Mapping[str, Any]]) -> bool:
    return any(str(diagnostic.get("severity", "")).lower() == "error" for diagnostic in diagnostics)


def _lint_summary(diagnostics: Sequence[Mapping[str, Any]]) -> str:
    if not diagnostics:
        return "retention lint passed"
    if _has_error_diagnostic(diagnostics):
        return "retention lint emitted error diagnostics"
    return "retention lint emitted advisory diagnostics"


def _engagement_lines(beats: Sequence[Any]) -> list[str]:
    lines: list[str] = []
    for beat in beats:
        engagement = _get(beat, "engagement", None)
        line = str(_get(engagement, "line", "") or "").strip()
        if line:
            lines.append(line)
    return lines


def _cut_ins_from_beats(beats: Sequence[Any]) -> list[dict[str, Any]]:
    cut_ins: list[dict[str, Any]] = []
    for index, beat in enumerate(beats):
        cutin = _get(beat, "cutin", _get(beat, "cut_in", None))
        if cutin is None:
            continue
        payload = _dump_value(cutin)
        if isinstance(payload, dict):
            cut_ins.append({"beat_index": index, **payload})
    return cut_ins


def _planner_rationale(
    *,
    blueprint: Any | None = None,
    strategy: Any | None = None,
    mined_candidates: Sequence[Any] | None = None,
    accepted_candidates: Sequence[Any] | None = None,
) -> dict[str, Any]:
    rationale: dict[str, Any] = {}

    mined = _candidate_rationale_items(mined_candidates or [])
    if mined:
        rationale["mine"] = {"candidates": mined}

    accepted = _candidate_rationale_items(accepted_candidates or [])
    if accepted:
        rationale["accepted_candidates"] = {"candidates": accepted}

    strategy_reason = _nonempty_str(_get(strategy, "rationale", None))
    if strategy_reason:
        rationale["strategize"] = {
            "rationale": strategy_reason,
            "template": _wire(_get(strategy, "template_", _get(strategy, "template", None))),
            "target_duration_s": _optional_float(_get(strategy, "target_duration_s", None)),
            "hook": _dump_value(_get(strategy, "hook", {})),
            "engagement_primary": _wire(_get(strategy, "engagement_primary", None)),
            "cta": _dump_value(_get(strategy, "cta", {})),
        }

    arrange_reason = _nonempty_str(_get(blueprint, "rationale", None))
    if arrange_reason:
        rationale["arrange"] = {
            "rationale": arrange_reason,
            "loop": _dump_value(_get(blueprint, "loop", {})),
            "engagement_lines": _engagement_lines(list(_get(blueprint, "beats", []) or [])),
            "cta": _dump_value(_get(blueprint, "cta", {})),
        }

    return rationale


def _candidate_rationale_items(candidates: Sequence[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        reason = _nonempty_str(_get(candidate, "rationale", None))
        if not reason:
            continue
        item: dict[str, Any] = {
            "index": index,
            "quote": _optional_str(_get(candidate, "quote", None)),
            "value_score": _optional_float(_get(candidate, "value_score", None)),
            "emotion": _optional_str(_get(candidate, "emotion", None)),
            "is_claim": _get(candidate, "is_claim", None),
            "payoff_worthy": _get(candidate, "payoff_worthy", None),
            "rationale": reason,
        }
        candidate_id = _optional_str(_get(candidate, "candidate_id", None))
        if candidate_id:
            item["candidate_id"] = candidate_id
        items.append(item)
    return items


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return _dump_value(value)


def _dump_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return {str(key): _dump_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, tuple):
        return [_dump_value(item) for item in value]
    return value


def _wire(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
