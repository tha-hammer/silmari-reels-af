from __future__ import annotations

import json
from pathlib import Path

from reel_af.dsl.models import DslWord, WordsSidecar
from reel_af.planner.eval.gates import evaluate_artifact_triple, evaluate_blueprint_pre_gates
from reel_af.planner.eval.models import RETENTION_DIMENSIONS, DimensionScore, JudgeResult
from reel_af.planner.eval.runner import (
    diff_eval_runs,
    score_artifact_dir,
    score_blueprint,
    write_eval_result,
)
from reel_af.planner.models import (
    Beat,
    BeatRole,
    CtaHardness,
    CtaPlan,
    CutIn,
    CutInKind,
    Engagement,
    EngagementKind,
    Hook,
    HookType,
    Interrupt,
    InterruptKind,
    LoopPlan,
    ReelBlueprint,
    Template,
    XfadeEffect,
)
from reel_af.planner.serialize import build_hook_plan, resolve_timecodes, serialize_composite
from tests.planner.factories import arc_plan, duration_policy, duration_range

SRC = "https://youtu.be/eval123"
BASELINE = Path(__file__).resolve().parent / "fixtures" / "BASELINE-0"


class _NeverJudge:
    def score(self, evidence):  # pragma: no cover - called only if hard gates regress
        raise AssertionError("judge must not run after a deterministic pre-gate failure")


class _FixedJudge:
    def __init__(self, score: int = 3) -> None:
        self.model = "fixed-judge"
        self.score_value = score
        self.evidence = None

    def score(self, evidence):
        self.evidence = evidence
        dimensions = {
            dimension: DimensionScore(score=self.score_value, rationale="fixed judge score")
            for dimension in RETENTION_DIMENSIONS
        }
        return JudgeResult(
            model=self.model,
            dimensions=dimensions,
            aggregate_score=float(self.score_value),
            raw_response='{"dimensions": "fixed"}',
        )


def _words() -> WordsSidecar:
    return WordsSidecar(
        words=[
            DslWord(w="pay", start=0.0, end=0.2),
            DslWord(w="now", start=0.25, end=0.45),
            DslWord(w="before", start=0.5, end=0.8),
            DslWord(w="launch", start=0.85, end=1.1),
            DslWord(w="process", start=1.5, end=1.8),
            DslWord(w="catches", start=1.85, end=2.15),
            DslWord(w="drift", start=2.2, end=2.5),
            DslWord(w="early", start=2.55, end=2.85),
            DslWord(w="tighter", start=3.2, end=3.5),
            DslWord(w="loops", start=3.55, end=3.8),
            DslWord(w="expose", start=3.85, end=4.1),
            DslWord(w="mistakes", start=4.15, end=4.45),
            DslWord(w="faster", start=4.5, end=4.8),
            DslWord(w="pay", start=5.1, end=5.3),
            DslWord(w="now", start=5.35, end=5.55),
            DslWord(w="protects", start=5.6, end=5.9),
            DslWord(w="launch", start=5.95, end=6.2),
        ]
    )


def _write_words(tmp_path: Path) -> Path:
    path = tmp_path / "words.json"
    path.write_text(_words().model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _blueprint(
    *,
    cta_placements: list[str] | None = None,
    engagement_line: str = "Send this to a founder before launch.",
    include_cutin: bool = False,
    rationale: str | None = "the order sets up launch risk, tightens through process, and loops pay now",
) -> ReelBlueprint:
    return ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(
            min_s=5.0,
            max_s=8.0,
            rationale="the launch-risk arc is complete without padding inside this range",
        ),
        duration_policy=duration_policy(advisory_min_s=1.0, advisory_max_s=10.0),
        arc=arc_plan(
            required_candidate_ids=("c001", "c002", "c003", "c004"),
            completion_criteria=(
                "hook establishes the launch-risk promise",
                "context explains how process catches drift",
                "proof shows tighter loops expose mistakes",
                "payoff resolves why paying now protects launch",
            ),
        ),
        hook=Hook(
            type=HookType.BoldClaim,
            banner_line="Pay now before launch.",
            span_quote="pay now before launch",
            candidate_id="c001",
            occurrence_index=0,
        ),
        beats=[
            Beat(
                role=BeatRole.Hook,
                span_quote="pay now before launch",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Dissolve,
                    dur_s=0.2,
                ),
            ),
            Beat(
                role=BeatRole.Context,
                span_quote="process catches drift early",
                candidate_id="c002",
                occurrence_index=0,
                max_len_s=2.0,
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Smoothleft,
                    dur_s=0.2,
                ),
            ),
            Beat(
                role=BeatRole.Value,
                span_quote="tighter loops expose mistakes faster",
                candidate_id="c003",
                occurrence_index=0,
                max_len_s=1.7,
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Fade,
                    dur_s=0.2,
                ),
                engagement=Engagement(
                    kind=EngagementKind.Send,
                    line=engagement_line,
                    primary=True,
                ),
                cutin=(
                    CutIn(
                        type=CutInKind.Zoom,
                        offset_s=0.2,
                        dur_s=0.4,
                        zoom_focus="mistakes faster",
                    )
                    if include_cutin
                    else None
                ),
            ),
            Beat(
                role=BeatRole.Payoff,
                span_quote="pay now protects launch",
                candidate_id="c004",
                occurrence_index=0,
                max_len_s=1.3,
            ),
        ],
        loop=LoopPlan(
            strategy="echo_hook_language",
            final_span_quote="pay now protects launch",
            candidate_id="c004",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=cta_placements or ["end"]),
        rationale=rationale,
        completion_rationale=(
            "hook establishes the launch-risk promise, context explains process drift, "
            "proof shows tighter loops expose mistakes, and payoff resolves why paying now "
            "protects launch"
        ),
    )


def test_blueprint_pre_gates_pass_for_clean_reel():
    gates, evidence, refs = evaluate_blueprint_pre_gates(
        _blueprint(),
        _words(),
        source_url=SRC,
        case_id="clean",
    )

    assert gates.passed
    assert refs == {}
    assert {check.name: check.passed for check in gates.checks} == {
        "verbatim_align": True,
        "retention_lint": True,
        "compile": True,
    }
    assert evidence.beat_count == 4
    assert evidence.duration_range_s["max_s"] == 8.0
    assert evidence.duration_policy["effective_cap_s"] == 180.0
    assert evidence.estimated_duration_s is not None and evidence.estimated_duration_s > 0
    assert evidence.compiled_duration_s is not None and evidence.compiled_duration_s > 0
    assert evidence.completion_rationale
    assert evidence.beats[0].duration_s <= 3.5
    assert evidence.loop_final_span_quote == "pay now protects launch"


def test_pre_gate_failure_scores_zero_without_calling_judge(tmp_path):
    result = score_blueprint(
        _blueprint(engagement_line="comment yes for the checklist"),
        _write_words(tmp_path),
        source_url=SRC,
        case_id="engagement-bait",
        judge=_NeverJudge(),
    )

    assert result.aggregate_score == 0
    assert result.judge_skipped
    assert all(score.score == 0 for score in result.dimensions.values())
    assert any(
        diagnostic.get("rule") == "R11" and diagnostic.get("severity") == "error"
        for check in result.gates.checks
        for diagnostic in check.diagnostics
    )


def test_warning_lint_does_not_skip_judge(tmp_path):
    result = score_blueprint(
        _blueprint(cta_placements=["middle", "end"]),
        _write_words(tmp_path),
        source_url=SRC,
        case_id="warning-only",
        judge=_FixedJudge(score=3),
    )

    assert result.aggregate_score == 3
    assert not result.judge_skipped
    lint_check = next(check for check in result.gates.checks if check.name == "retention_lint")
    assert lint_check.passed
    assert any(
        diagnostic.get("rule") == "R12" and diagnostic.get("severity") == "warning"
        for diagnostic in lint_check.diagnostics
    )


def test_artifact_triple_reads_blueprint_sidecars(tmp_path):
    words = _words()
    blueprint = _blueprint(include_cutin=True, rationale="arrange reason")
    resolved = resolve_timecodes(blueprint.beats, words)
    composite_path = tmp_path / "composite.ts.md"
    hook_path = tmp_path / "hook-plan.json"
    words_path = tmp_path / "transcript.words.json"

    composite_path.write_text(serialize_composite(blueprint, resolved), encoding="utf-8")
    words_path.write_text(words.model_dump_json(indent=2) + "\n", encoding="utf-8")
    hook_plan = build_hook_plan(
        source_url=SRC,
        hook=blueprint.hook,
        span=resolved,
        cut_ins=[],
        composite_ref=str(composite_path),
        model="test-model",
        duration_bounds_s={"min": 1, "max": 10},
    )
    hook_path.write_text(json.dumps(hook_plan, indent=2) + "\n", encoding="utf-8")
    (tmp_path / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "strategy.json").write_text(
        json.dumps(
            {
                "template_": "hook_context_value_payoff_cta",
                "duration_range_s": blueprint.duration_range_s.model_dump(mode="json"),
                "duration_policy": blueprint.duration_policy.model_dump(mode="json"),
                "arc": blueprint.arc.model_dump(mode="json"),
                "hook": blueprint.hook.model_dump(mode="json"),
                "engagement_primary": "send",
                "cta": {"hardness": "soft", "placements": ["end"]},
                "rationale": "strategy reason",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "mined-candidates.json").write_text(
        json.dumps(
            [
                {
                    "quote": "pay now before launch",
                    "value_score": 0.91,
                    "emotion": "urgency",
                    "is_claim": True,
                    "payoff_worthy": True,
                    "rationale": "mine reason",
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "accepted-candidates.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "c001",
                    "quote": "pay now before launch",
                    "start_s": 0.0,
                    "end_s": 1.1,
                    "source_window_id": "window-000",
                    "source_window_index": 0,
                    "source_window_start_s": 0.0,
                    "source_window_end_s": 2.0,
                    "value_score": 0.91,
                    "emotion": "urgency",
                    "is_claim": True,
                    "payoff_worthy": True,
                    "rationale": "accepted reason",
                },
                {
                    "candidate_id": "c003",
                    "quote": "tighter loops expose mistakes faster",
                    "start_s": 3.2,
                    "end_s": 4.8,
                    "source_window_id": "window-001",
                    "source_window_index": 1,
                    "source_window_start_s": 2.0,
                    "source_window_end_s": 4.5,
                    "value_score": 0.84,
                    "emotion": "clarity",
                    "is_claim": False,
                    "payoff_worthy": False,
                    "rationale": "mid-source proof",
                },
                {
                    "candidate_id": "c004",
                    "quote": "pay now protects launch",
                    "start_s": 5.1,
                    "end_s": 6.2,
                    "source_window_id": "window-002",
                    "source_window_index": 2,
                    "source_window_start_s": 4.5,
                    "source_window_end_s": 6.2,
                    "value_score": 0.88,
                    "emotion": "relief",
                    "is_claim": True,
                    "payoff_worthy": True,
                    "rationale": "late-source payoff",
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    gates, evidence, refs = evaluate_artifact_triple(
        composite_path,
        hook_path,
        words_path,
        case_id="sidecar",
        source_url=SRC,
    )

    assert gates.passed
    assert refs["blueprint_ref"].endswith("blueprint.json")
    assert evidence.beat_count == 4
    assert evidence.duration_range_s["max_s"] == 8.0
    assert evidence.duration_policy["effective_cap_s"] == 180.0
    assert evidence.estimated_duration_s is not None and evidence.estimated_duration_s > 0
    assert evidence.compiled_duration_s is not None and evidence.compiled_duration_s > 0
    assert evidence.completion_rationale
    assert evidence.candidate_source_coverage["early"] == 1
    assert evidence.candidate_source_coverage["mid"] == 1
    assert evidence.candidate_source_coverage["late"] == 1
    assert len(evidence.candidate_source_coverage["windows"]) == 3
    assert evidence.engagement_lines == ["Send this to a founder before launch."]
    assert evidence.cta["hardness"] in {"Soft", "soft"}
    assert evidence.cut_ins[0]["beat_index"] == 2
    assert evidence.planner_rationale["mine"]["candidates"][0]["rationale"] == "mine reason"
    assert (
        evidence.planner_rationale["accepted_candidates"]["candidates"][0]["rationale"]
        == "accepted reason"
    )
    assert evidence.planner_rationale["strategize"]["rationale"] == "strategy reason"
    assert evidence.planner_rationale["strategize"]["arc"]["required_candidate_ids"] == [
        "c001",
        "c002",
        "c003",
        "c004",
    ]
    assert evidence.planner_rationale["arrange"]["rationale"] == "arrange reason"
    assert evidence.planner_rationale["arrange"]["completion_rationale"]


def test_baseline_zero_fixture_is_committed_and_scores_with_warning_only_lint(tmp_path):
    result = score_artifact_dir(
        BASELINE,
        case_id="BASELINE-0",
        judge=_FixedJudge(score=2),
        out_dir=tmp_path,
    )

    assert result.case_id == "BASELINE-0"
    assert result.artifact_kind == "triple"
    assert result.aggregate_score == 2
    assert not result.judge_skipped
    assert result.output_refs["composite_ref"].endswith("BASELINE-0/composite.ts.md")
    assert not any(
        diagnostic.get("severity") == "error"
        for check in result.gates.checks
        for diagnostic in check.diagnostics
    )
    assert any(
        diagnostic.get("severity") == "warning"
        for check in result.gates.checks
        for diagnostic in check.diagnostics
    )
    assert list(tmp_path.glob("*BASELINE-0.json"))


def test_timestamped_results_and_diff(tmp_path):
    left = score_artifact_dir(BASELINE, case_id="BASELINE-0", judge=_FixedJudge(score=1))
    left = left.model_copy(update={"run_id": "left"})
    right_dimensions = {
        dimension: DimensionScore(score=2, rationale="synthetic comparison score")
        for dimension in RETENTION_DIMENSIONS
    }
    right = left.model_copy(
        update={
            "run_id": "right",
            "aggregate_score": 2.0,
            "dimensions": right_dimensions,
        }
    )

    left_path = write_eval_result(left, tmp_path)
    right_path = write_eval_result(right, tmp_path)
    diff = diff_eval_runs(left_path, right_path)

    assert diff.aggregate_delta == 1.0
    assert diff.dimension_deltas["hook_strength_r1"]["delta"] == 1.0
