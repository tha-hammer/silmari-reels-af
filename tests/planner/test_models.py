from __future__ import annotations

import pytest
from pydantic import ValidationError

from reel_af.planner.models import Beat, CtaPlan, Hook, Interrupt, LoopPlan, ReelBlueprint


def test_blueprint_round_trips():
    bp = ReelBlueprint(
        template="hook_context_value_payoff_cta",
        target_duration_s=28.0,
        hook=Hook(
            type="curiosity_gap",
            banner_line="They fake it.",
            span_quote="they pattern match",
        ),
        beats=[
            Beat(
                role="hook",
                span_quote="they pattern match",
                max_len_s=3.0,
                interrupt_out=Interrupt(kind="trans", effect="dissolve", dur_s=0.5),
            )
        ],
        loop=LoopPlan(strategy="tie_final_to_hook", final_span_quote="they pattern match"),
        engagement_primary="send",
        cta=CtaPlan(hardness="soft", placements=["end"]),
    )

    assert ReelBlueprint.model_validate(bp.model_dump()) == bp


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        ReelBlueprint.model_validate({"template": "listicle", "surprise": 1})


def test_interrupt_kind_excludes_cutin():
    with pytest.raises(ValidationError):
        Interrupt.model_validate({"kind": "cutin"})


def test_interrupt_effect_reuses_dsl_effect_values():
    with pytest.raises(ValidationError):
        Interrupt.model_validate({"kind": "trans", "effect": "wipe", "dur_s": 0.5})


def test_none_transition_requires_zero_duration():
    with pytest.raises(ValidationError):
        Interrupt.model_validate({"kind": "trans", "effect": "none", "dur_s": 0.5})
