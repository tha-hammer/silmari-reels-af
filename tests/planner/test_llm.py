from __future__ import annotations

from reel_af.planner.llm import FakePlannerLLM, NeverPlannerLLM, PlannerLLM
from reel_af.planner.models import (
    Beat,
    CandidateSpan,
    CtaPlan,
    Hook,
    LoopPlan,
    ReelBlueprint,
    ReelStrategy,
)


def _blueprint() -> ReelBlueprint:
    return ReelBlueprint(
        template="hook_context_value_payoff_cta",
        target_duration_s=24.0,
        hook=Hook(
            type="curiosity_gap",
            banner_line="They fake it.",
            span_quote="they pattern match",
        ),
        beats=[Beat(role="hook", span_quote="they pattern match", max_len_s=3.0)],
        loop=LoopPlan(strategy="tie_final_to_hook", final_span_quote="they pattern match"),
        engagement_primary="send",
        cta=CtaPlan(hardness="soft", placements=["end"]),
    )


async def test_fake_planner_llm_returns_typed_structs():
    candidate = CandidateSpan(
        quote="they pattern match",
        approx_start_s=4.1,
        approx_end_s=5.0,
        value_score=0.9,
        emotion="skepticism",
        is_claim=True,
        payoff_worthy=True,
    )
    strategy = ReelStrategy(
        template="hook_context_value_payoff_cta",
        target_duration_s=24.0,
        hook=_blueprint().hook,
        engagement_primary="send",
        cta=CtaPlan(hardness="soft", placements=["end"]),
    )
    fake: PlannerLLM = FakePlannerLLM(
        candidates=[candidate],
        strategy=strategy,
        blueprint=_blueprint(),
    )

    candidates = await fake.mine("transcript", "educational")
    planned = await fake.strategize("transcript", candidates, {"min_s": 15, "max_s": 45})
    arranged = await fake.arrange(candidates, planned)

    assert candidates == [candidate]
    assert planned == strategy
    assert arranged == _blueprint()
    assert fake.calls == [
        ("mine", "educational"),
        ("strategize", {"min_s": 15, "max_s": 45}),
        ("arrange",),
    ]


async def test_never_planner_llm_raises_if_called():
    llm = NeverPlannerLLM()

    try:
        await llm.arrange([], None)
    except AssertionError as exc:
        assert "must not be called" in str(exc)
    else:
        raise AssertionError("NeverPlannerLLM.arrange should raise")
