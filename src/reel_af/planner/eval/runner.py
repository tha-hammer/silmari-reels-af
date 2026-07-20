"""Runner and CLI for reel-quality evals."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reel_af.dsl.compile import load_words
from reel_af.planner.models import ReelBlueprint
from reel_af.planner.paths import evals_dir

from .gates import evaluate_artifact_triple, evaluate_blueprint_pre_gates
from .judge import OpenRouterJudge
from .models import (
    RETENTION_DIMENSIONS,
    EvalDiff,
    EvalResult,
    JudgeResult,
    zero_dimension_scores,
)


def score_blueprint(
    blueprint: Any,
    words_path: str | Path,
    *,
    source_url: str,
    case_id: str,
    register: str | None = None,
    judge: OpenRouterJudge | None = None,
    out_dir: str | Path | None = None,
) -> EvalResult:
    """Score a produced `ReelBlueprint` and optionally persist timestamped JSON."""

    words = load_words(words_path)
    gates, evidence, refs = evaluate_blueprint_pre_gates(
        blueprint,
        words,
        source_url=source_url,
        case_id=case_id,
        register=register,
    )
    result = _score_after_gates(
        gates=gates,
        evidence=evidence,
        refs={**refs, "words_ref": str(words_path)},
        judge=judge,
    )
    if out_dir is not None:
        write_eval_result(result, out_dir)
    return result


def score_artifact_dir(
    fixture_dir: str | Path,
    *,
    case_id: str | None = None,
    source_url: str | None = None,
    register: str | None = None,
    judge: OpenRouterJudge | None = None,
    out_dir: str | Path | None = None,
) -> EvalResult:
    """Score a persisted production triple directory."""

    fixture_dir = Path(fixture_dir)
    case_id = case_id or fixture_dir.name
    gates, evidence, refs = evaluate_artifact_triple(
        fixture_dir / "composite.ts.md",
        fixture_dir / "hook-plan.json",
        fixture_dir / "transcript.words.json",
        case_id=case_id,
        source_url=source_url,
        register=register,
    )
    result = _score_after_gates(gates=gates, evidence=evidence, refs=refs, judge=judge)
    if out_dir is not None:
        write_eval_result(result, out_dir)
    return result


def diff_eval_runs(left: str | Path | EvalResult, right: str | Path | EvalResult) -> EvalDiff:
    """Return per-dimension and aggregate deltas for two eval run JSON files."""

    left_result = _load_result(left)
    right_result = _load_result(right)
    dimension_deltas = {
        dimension: {
            "left": float(left_result.dimensions[dimension].score),
            "right": float(right_result.dimensions[dimension].score),
            "delta": float(right_result.dimensions[dimension].score)
            - float(left_result.dimensions[dimension].score),
        }
        for dimension in RETENTION_DIMENSIONS
    }
    left_gates = {check.name: check.passed for check in left_result.gates.checks}
    right_gates = {check.name: check.passed for check in right_result.gates.checks}
    gate_changes = {
        name: {"left": left_gates.get(name, False), "right": right_gates.get(name, False)}
        for name in sorted(set(left_gates) | set(right_gates))
        if left_gates.get(name, False) != right_gates.get(name, False)
    }
    return EvalDiff(
        created_at=_now_iso(),
        left_run_id=left_result.run_id,
        right_run_id=right_result.run_id,
        aggregate_delta=round(right_result.aggregate_score - left_result.aggregate_score, 3),
        dimension_deltas=dimension_deltas,
        gate_changes=gate_changes,
    )


def write_eval_result(result: EvalResult, out_dir: str | Path) -> Path:
    """Write one timestamped eval JSON result."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{_slug(result.run_id)}.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def write_eval_diff(diff: EvalDiff, out_path: str | Path) -> Path:
    """Write one eval diff JSON artifact."""

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diff.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _score_after_gates(
    *,
    gates: Any,
    evidence: Any,
    refs: dict[str, str],
    judge: OpenRouterJudge | None,
) -> EvalResult:
    created_at = _now_iso()
    run_id = f"{_timestamp()}-{_slug(evidence.case_id)}"
    if not gates.passed:
        dimensions = zero_dimension_scores("deterministic pre-gate failed; judge not run")
        return EvalResult(
            run_id=run_id,
            created_at=created_at,
            case_id=evidence.case_id,
            artifact_kind=evidence.artifact_kind,
            source_url=evidence.source_url,
            judge_model=None,
            judge_skipped=True,
            gates=gates,
            dimensions=dimensions,
            aggregate_score=0.0,
            planner_rationale=evidence.planner_rationale,
            output_refs=refs,
            metadata={"evidence": evidence.model_dump(mode="json", exclude_none=True)},
        )

    judge = judge or OpenRouterJudge()
    judged: JudgeResult = judge.score(evidence)
    return EvalResult(
        run_id=run_id,
        created_at=created_at,
        case_id=evidence.case_id,
        artifact_kind=evidence.artifact_kind,
        source_url=evidence.source_url,
        judge_model=judged.model,
        judge_skipped=False,
        gates=gates,
        dimensions=judged.dimensions,
        aggregate_score=judged.aggregate_score,
        planner_rationale=evidence.planner_rationale,
        output_refs=refs,
        metadata={
            "evidence": evidence.model_dump(mode="json", exclude_none=True),
            "judge_raw_response": judged.raw_response,
        },
    )


def _load_result(value: str | Path | EvalResult) -> EvalResult:
    if isinstance(value, EvalResult):
        return value
    return EvalResult.model_validate_json(Path(value).read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")
    return slug or "eval"


def _cmd_score_artifacts(args: argparse.Namespace) -> None:
    result = score_artifact_dir(
        args.fixture_dir,
        case_id=args.case_id,
        source_url=args.source_url,
        register=args.register,
    )
    path = write_eval_result(result, args.out_dir or evals_dir())
    print(json.dumps({"result_path": str(path), "aggregate_score": result.aggregate_score}))


def _cmd_score_blueprint(args: argparse.Namespace) -> None:
    blueprint = ReelBlueprint.model_validate_json(Path(args.blueprint).read_text(encoding="utf-8"))
    result = score_blueprint(
        blueprint,
        args.words,
        source_url=args.source_url,
        case_id=args.case_id,
        register=args.register,
    )
    path = write_eval_result(result, args.out_dir or evals_dir())
    print(json.dumps({"result_path": str(path), "aggregate_score": result.aggregate_score}))


def _cmd_diff(args: argparse.Namespace) -> None:
    diff = diff_eval_runs(args.left, args.right)
    path = write_eval_diff(diff, args.out or _default_diff_path(args.left, args.right))
    print(json.dumps({"diff_path": str(path), "aggregate_delta": diff.aggregate_delta}))


def _default_diff_path(left: str | Path, right: str | Path) -> Path:
    left_slug = _slug(Path(left).stem)
    right_slug = _slug(Path(right).stem)
    return evals_dir() / f"{left_slug}__{right_slug}.json"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run reel-quality evals.")
    sub = parser.add_subparsers(required=True)

    artifacts = sub.add_parser("score-artifacts", help="score a production triple directory")
    artifacts.add_argument("fixture_dir")
    artifacts.add_argument("--case-id")
    artifacts.add_argument("--source-url")
    artifacts.add_argument("--register")
    artifacts.add_argument("--out-dir")
    artifacts.set_defaults(func=_cmd_score_artifacts)

    blueprint = sub.add_parser("score-blueprint", help="score a ReelBlueprint JSON file")
    blueprint.add_argument("--blueprint", required=True)
    blueprint.add_argument("--words", required=True)
    blueprint.add_argument("--source-url", required=True)
    blueprint.add_argument("--case-id", required=True)
    blueprint.add_argument("--register")
    blueprint.add_argument("--out-dir")
    blueprint.set_defaults(func=_cmd_score_blueprint)

    diff = sub.add_parser("diff", help="diff two eval result JSON files")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--out")
    diff.set_defaults(func=_cmd_diff)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
