from __future__ import annotations

import pytest
from pydantic import ValidationError

from baml_client import types as baml_types
from reel_af.planner.models import (
    ArcPlan,
    Beat,
    CandidateSpan,
    CtaPlan,
    CutIn,
    CutInKind,
    DurationPolicy,
    DurationRange,
    Hook,
    HookType,
    Interrupt,
    InterruptKind,
    LoopPlan,
    ReelBlueprint,
    ReelStrategy,
    Template,
    XfadeEffect,
    interrupt_marker,
    validate_candidate_span,
    validate_cut_in,
    validate_interrupt,
)
from tests.planner.factories import arc_plan, duration_policy, duration_range


def test_models_facade_reexports_generated_baml_types():
    assert CandidateSpan is baml_types.CandidateSpan
    assert Hook is baml_types.Hook
    assert Interrupt is baml_types.Interrupt
    assert CutIn is baml_types.CutIn
    assert Beat is baml_types.Beat
    assert ReelStrategy is baml_types.ReelStrategy
    assert ReelBlueprint is baml_types.ReelBlueprint
    assert DurationPolicy is baml_types.DurationPolicy
    assert DurationRange is baml_types.DurationRange
    assert ArcPlan is baml_types.ArcPlan
    assert HookType is baml_types.HookType
    assert Template is baml_types.Template


def test_hooktype_has_spec_eight_baml_members():
    assert set(HookType.__members__) == {
        "CuriosityGap",
        "BoldClaim",
        "DirectCallout",
        "ResultFirst",
        "Question",
        "PainPoint",
        "Number",
        "PatternInterrupt",
    }


def test_candidate_span_approx_offsets_are_optional_hints():
    span = CandidateSpan(quote="hello world", value_score=0.9)

    assert span.quote == "hello world"
    assert span.approx_end_s is None


def test_candidate_span_semantic_validator_checks_ordered_offsets():
    span = CandidateSpan(quote="hello world", approx_start_s=1.0, approx_end_s=2.0, value_score=0.9)
    assert validate_candidate_span(span) is span

    bad = CandidateSpan(quote="hello world", approx_start_s=2.0, approx_end_s=1.0, value_score=0.9)
    with pytest.raises(ValueError, match="approx_end_s"):
        validate_candidate_span(bad)


def test_hook_beat_and_loop_require_candidate_identity_from_baml_shape():
    hook = Hook(
        type=HookType.CuriosityGap,
        banner_line="Hello",
        span_quote="hello world",
        candidate_id="c001",
        occurrence_index=0,
    )
    beat = Beat(
        role=baml_types.BeatRole.Hook,
        span_quote="hello world",
        candidate_id="c001",
        occurrence_index=0,
        max_len_s=3.0,
    )
    loop = LoopPlan(
        strategy="tie_final_to_hook",
        final_span_quote="hello world",
        candidate_id="c001",
        occurrence_index=0,
    )

    assert hook.candidate_id == "c001"
    assert hook.occurrence_index == 0
    assert beat.candidate_id == "c001"
    assert beat.occurrence_index == 0
    assert loop.candidate_id == "c001"
    assert loop.occurrence_index == 0


def test_cut_in_is_relative_and_semantic_validator_checks_payload():
    cut = CutIn(type=CutInKind.Zoom, offset_s=0.5, dur_s=1.0)

    assert cut.offset_s == 0.5
    assert cut.dur_s == 1.0
    assert not hasattr(cut, "at_s")
    assert not hasattr(cut, "until_s")
    assert validate_cut_in(cut) is cut

    with pytest.raises(ValueError, match="visual cut-in"):
        validate_cut_in(CutIn(type=CutInKind.Visual, offset_s=0.5, dur_s=1.0))

    with pytest.raises(ValueError, match="duration"):
        validate_cut_in(CutIn(type=CutInKind.Zoom, offset_s=0.5, dur_s=0.0))


def test_blueprint_round_trips():
    bp = ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=18.0, max_s=28.0),
        duration_policy=duration_policy(),
        arc=arc_plan(),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="They fake it.",
            span_quote="they pattern match",
            candidate_id="c001",
            occurrence_index=0,
        ),
        beats=[
            Beat(
                role=baml_types.BeatRole.Hook,
                span_quote="they pattern match",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Dissolve,
                    dur_s=0.5,
                ),
            )
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote="they pattern match",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=baml_types.EngagementKind.Send,
        cta=CtaPlan(hardness=baml_types.CtaHardness.Soft, placements=["end"]),
        completion_rationale="hook, proof, payoff, and loop criteria are covered",
    )

    assert ReelBlueprint.model_validate(bp.model_dump()) == bp
    assert bp.model_dump()["template_"] == "HookContextValuePayoffCta"
    assert "template" not in ReelBlueprint.model_fields
    assert "target_duration_s" not in ReelBlueprint.model_fields


def test_strategy_uses_template_field_not_template_suffix():
    strategy = ReelStrategy(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(),
        duration_policy=duration_policy(),
        arc=arc_plan(),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="Hello",
            span_quote="hello world",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=baml_types.EngagementKind.Save,
        cta=CtaPlan(hardness=baml_types.CtaHardness.Soft, placements=["end"]),
    )

    assert strategy.template_ is Template.HookContextValuePayoffCta
    assert "template" not in ReelStrategy.model_fields
    assert "target_duration_s" not in ReelStrategy.model_fields
    assert strategy.model_dump()["template_"] == "HookContextValuePayoffCta"


def test_generated_types_allow_extra_fields_from_baml_runtime():
    bp = ReelBlueprint.model_validate(
        {
            "template_": "Listicle",
            "duration_range_s": {
                "min_s": 18.0,
                "max_s": 28.0,
                "rationale": "one list arc completes without padding",
            },
            "duration_policy": {
                "soft_cap_s": 180.0,
                "effective_cap_s": 180.0,
                "advisory_min_s": 10.0,
                "advisory_max_s": 180.0,
                "cap_overridden": False,
            },
            "arc": {
                "promise": "three ideas",
                "thread": "one list thread",
                "completion_criteria": ["hook", "payoff", "loop"],
                "required_candidate_ids": ["c001"],
                "optional_candidate_ids": [],
                "excluded_candidate_ids": [],
            },
            "hook": {
                "type": "Number",
                "banner_line": "Three ideas",
                "span_quote": "three ideas",
                "candidate_id": "c001",
                "occurrence_index": 0,
            },
            "beats": [
                {
                    "role": "Hook",
                    "span_quote": "three ideas",
                    "candidate_id": "c001",
                    "occurrence_index": 0,
                    "max_len_s": 3.0,
                }
            ],
            "loop": {
                "strategy": "tie_final_to_hook",
                "final_span_quote": "three ideas",
                "candidate_id": "c001",
                "occurrence_index": 0,
            },
            "engagement_primary": "Save",
            "cta": {"hardness": "Soft", "placements": ["end"]},
            "completion_rationale": "the list hook, payoff, and loop are covered",
            "surprise": 1,
        }
    )

    assert bp.template_ is Template.Listicle


def test_interrupt_kind_excludes_cutin():
    with pytest.raises(ValidationError):
        Interrupt.model_validate({"kind": "cutin"})


def test_interrupt_effect_reuses_dsl_effect_values():
    with pytest.raises(ValidationError):
        Interrupt.model_validate({"kind": "Trans", "effect": "wipe", "dur_s": 0.5})


def test_interrupt_semantic_helpers_default_and_validate_transition():
    trans = validate_interrupt(Interrupt(kind=InterruptKind.Trans, effect=None, dur_s=None))

    assert trans.effect is XfadeEffect.Fade
    assert trans.dur_s == 0.0
    assert interrupt_marker(trans) == "trans"
    assert interrupt_marker(Interrupt(kind=InterruptKind.Black, effect=None, dur_s=0.5)) == "insert"

    with pytest.raises(ValueError, match='effect="none"'):
        validate_interrupt(
            Interrupt(kind=InterruptKind.Trans, effect=XfadeEffect.NoEffect, dur_s=0.5)
        )
