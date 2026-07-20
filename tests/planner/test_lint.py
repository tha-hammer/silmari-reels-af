from __future__ import annotations

from reel_af.dsl.models import DslWord, WordsSidecar
from reel_af.planner.config import PlannerConfig
from reel_af.planner.lint import lint_blueprint
from reel_af.planner.models import (
    Beat,
    BeatRole,
    CtaHardness,
    CtaPlan,
    EngagementKind,
    Hook,
    HookType,
    LoopPlan,
    ReelBlueprint,
    Template,
)
from tests.planner.factories import arc_plan, duration_policy, duration_range


def _cfg(**overrides) -> PlannerConfig:
    data = {
        "model": "test-model",
        "default_register": "educational",
        "max_repair_passes": 1,
        "r1_hook_window_s": 3.5,
        "r2_cadence_s": {"entertainment": 3.0, "educational": 5.0, "b2b": 9.0},
        "r4_max_gap_s": 0.6,
        "r8_min_token_overlap": 0.5,
        "r11_bait_patterns": [
            r"\bcomment\s+[a-z0-9_#-]+\b",
            r"\btag\s+\d+\b",
            r"\blike\s+if\b",
            r"\bcomment\s+\w+\s+for\b",
        ],
    }
    data.update(overrides)
    return PlannerConfig.model_validate(data)


def _blueprint(**overrides):
    bp = {
        "hook": {
            "banner_line": "send this to a dev who ships on Friday",
            "span_quote": "alpha beta",
        },
        "beats": [
            {
                "role": "hook",
                "span_quote": "alpha beta",
                "start_s": 1.0,
                "end_s": 2.0,
            },
            {
                "role": "value",
                "span_quote": "gamma delta",
                "start_s": 2.0,
                "end_s": 3.0,
            },
        ],
        "loop": {"final_span_quote": "alpha beta"},
        "cta": {"placements": ["end"]},
    }
    bp.update(overrides)
    return bp


def test_engagement_bait_hard_fails():
    bp = _blueprint(hook={"banner_line": "comment YES if you agree", "span_quote": "alpha beta"})

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R11" and d.severity == "error" for d in diags)


def test_clean_cta_passes_r11():
    diags = lint_blueprint(_blueprint(), words=None, cfg=_cfg())

    assert not any(d.rule == "R11" for d in diags)


def test_hook_window_over_threshold_warns():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "alpha", "duration_s": 2.0},
            {"role": "hook", "span_quote": "beta", "duration_s": 2.5},
            {"role": "value", "span_quote": "gamma", "duration_s": 1.0},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R1" and d.severity == "warning" for d in diags)


def test_baml_beat_role_member_drives_hook_window_lint():
    bp = ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=8.0, max_s=12.0),
        duration_policy=duration_policy(),
        arc=arc_plan(required_candidate_ids=("c001",)),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="Alpha beta",
            span_quote="alpha beta",
            candidate_id="c001",
            occurrence_index=0,
        ),
        beats=[
            Beat(
                role=BeatRole.Hook,
                span_quote="alpha",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=4.0,
            )
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote="alpha",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        completion_rationale="hook establishes the promise and payoff resolves the hook",
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R1" and d.severity == "warning" for d in diags)


def test_long_segment_without_change_warns():
    bp = _blueprint(
        beats=[
            {
                "role": "value",
                "span_quote": "alpha beta",
                "duration_s": 9.0,
                "interrupt_out": None,
                "cutin": None,
            }
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg(), register="entertainment")

    assert any(d.rule == "R2" and d.severity == "warning" for d in diags)


def test_internal_dead_air_warns():
    words = WordsSidecar(
        words=[
            DslWord(w="a", start=1.0, end=1.2),
            DslWord(w="b", start=2.1, end=2.3),
        ]
    )
    bp = _blueprint(beats=[{"role": "value", "span_quote": "a b", "start_s": 1.0, "end_s": 2.3}])

    diags = lint_blueprint(bp, words=words, cfg=_cfg())

    assert any(d.rule == "R4" and d.severity == "warning" for d in diags)


def test_final_not_echoing_hook_warns():
    bp = _blueprint(
        hook={"banner_line": "clean", "span_quote": "alpha beta"},
        loop={"final_span_quote": "zeta omega"},
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R8" and d.severity == "warning" for d in diags)


def test_non_decreasing_back_half_warns():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "a", "duration_s": 2.0},
            {"role": "context", "span_quote": "b", "duration_s": 3.0},
            {"role": "value", "span_quote": "c", "duration_s": 4.0},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R3" and d.severity == "warning" for d in diags)


def test_strictly_decreasing_back_half_passes_r3():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "a", "duration_s": 5.0},
            {"role": "context", "span_quote": "b", "duration_s": 4.0},
            {"role": "value", "span_quote": "c", "duration_s": 3.0},
            {"role": "payoff", "span_quote": "a", "duration_s": 2.0},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert not any(d.rule == "R3" for d in diags)


def test_long_reel_sectional_r3_allows_non_monotone_local_beats():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "a", "duration_s": 3.0},
            {"role": "context", "span_quote": "b", "duration_s": 6.0},
            {"role": "value", "span_quote": "c", "duration_s": 7.0},
            {"role": "value", "span_quote": "d", "duration_s": 6.5},
            {"role": "value", "span_quote": "e", "duration_s": 4.0},
            {"role": "payoff", "span_quote": "a", "duration_s": 4.5},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert not any(d.rule == "R3" for d in diags)


def test_r7_over_cap_errors_with_duration_context():
    policy = duration_policy(advisory_min_s=5.0, advisory_max_s=8.0, effective_cap_s=8.0)
    bp = _blueprint(
        duration_policy=policy.model_dump(mode="json"),
        arc=arc_plan(required_candidate_ids=("c001", "c002")).model_dump(mode="json"),
        completion_rationale="hook establishes promise and payoff resolves the hook",
        beats=[
            {"role": "hook", "span_quote": "alpha", "candidate_id": "c001", "duration_s": 6.0},
            {"role": "payoff", "span_quote": "alpha beta", "candidate_id": "c002", "duration_s": 6.0},
        ],
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg(), duration_policy=policy)

    over_cap = next(d for d in diags if d.rule == "R7" and d.severity == "error")
    assert "exceeds active cap" in over_cap.message
    assert over_cap.context["total_duration_s"] == 12.0
    assert over_cap.context["effective_cap_s"] == 8.0


def test_r7_missing_required_candidate_fails_completion_gate():
    bp = _blueprint(
        arc=arc_plan(required_candidate_ids=("c001", "c002")).model_dump(mode="json"),
        duration_policy=duration_policy().model_dump(mode="json"),
        completion_rationale="hook establishes promise and payoff resolves the hook",
        beats=[
            {"role": "hook", "span_quote": "alpha", "candidate_id": "c001", "duration_s": 2.0},
            {"role": "payoff", "span_quote": "alpha beta", "candidate_id": "c001", "duration_s": 2.0},
        ],
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(
        d.rule == "R7"
        and d.severity == "error"
        and "omits required arc candidates" in d.message
        for d in diags
    )


def test_r7_beat_count_guard_errors():
    cfg = _cfg(max_beats=1)
    bp = _blueprint(
        duration_policy=duration_policy().model_dump(mode="json"),
        arc=arc_plan(required_candidate_ids=("c001", "c002")).model_dump(mode="json"),
        completion_rationale="hook establishes promise and payoff resolves the hook",
        beats=[
            {"role": "hook", "span_quote": "alpha", "candidate_id": "c001", "duration_s": 2.0},
            {"role": "payoff", "span_quote": "alpha beta", "candidate_id": "c002", "duration_s": 2.0},
        ],
    )

    diags = lint_blueprint(bp, words=None, cfg=cfg)

    assert any(d.rule == "R7" and "max_beats" in d.message for d in diags)


def test_multiple_primary_ctas_warn():
    bp = _blueprint(cta={"placements": ["middle", "end"]})

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R12" and d.severity == "warning" for d in diags)
