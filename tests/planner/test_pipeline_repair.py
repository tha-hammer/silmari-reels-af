from __future__ import annotations

from pathlib import Path

from reel_af.dsl.models import DslWord, WordsSidecar
from reel_af.planner import pipeline as pipeline_mod
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.models import (
    Beat,
    BeatRole,
    CandidateSpan,
    CtaHardness,
    CtaPlan,
    EngagementKind,
    Hook,
    HookType,
    LoopPlan,
    ReelBlueprint,
    ReelStrategy,
    Template,
)
from reel_af.planner.pipeline import plan
from tests.planner.factories import arc_plan, duration_policy, duration_range

SRC = "https://www.youtube.com/watch?v=repair123"
EXPECTED_HINT = "candidate c001 below_floor: 'paraphrase' near 'verbatim words fix works'"


def _words() -> WordsSidecar:
    return WordsSidecar(
        words=[
            DslWord(w="verbatim", start=0.0, end=0.4),
            DslWord(w="words", start=0.5, end=0.9),
            DslWord(w="fix", start=1.0, end=1.4),
            DslWord(w="works", start=1.5, end=1.9),
        ]
    )


def _cfg(**overrides) -> PlannerConfig:
    data = load_planner_config().model_dump()
    data.update(overrides)
    return PlannerConfig.model_validate(data)


def _strategy() -> ReelStrategy:
    return ReelStrategy(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=8.0, max_s=12.0),
        duration_policy=duration_policy(),
        arc=arc_plan(required_candidate_ids=("c001",)),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="Verbatim words",
            span_quote="verbatim words",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
    )


def _blueprint(quote: str) -> ReelBlueprint:
    return ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=8.0, max_s=12.0),
        duration_policy=duration_policy(),
        arc=arc_plan(required_candidate_ids=("c001",)),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="Verbatim words",
            span_quote=quote,
            candidate_id="c001",
            occurrence_index=0,
        ),
        beats=[
            Beat(
                role=BeatRole.Hook,
                span_quote=quote,
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
            ),
            Beat(
                role=BeatRole.Payoff,
                span_quote="fix works",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
            )
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote="fix works",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        completion_rationale="hook establishes the promise and payoff resolves the hook",
    )


class _HintSensitiveLLM:
    def __init__(self, *, never_good: bool = False) -> None:
        self.never_good = never_good
        self.repair_hints: list[str | None] = []

    async def mine(self, transcript, register):
        return [
            CandidateSpan(
                quote="verbatim words fix works",
                approx_start_s=0.0,
                approx_end_s=1.9,
                value_score=0.9,
                emotion="plain",
                is_claim=False,
                payoff_worthy=True,
            )
        ]

    async def strategize(self, transcript, candidates, policy):
        return _strategy()

    async def arrange(self, candidates, strategy, *, repair_hint=None):
        self.repair_hints.append(repair_hint)
        if not self.never_good and repair_hint == EXPECTED_HINT:
            return _blueprint("verbatim words")
        return _blueprint("paraphrase")


async def test_repair_hint_reprompts_arrange_and_compiles_on_second_pass(tmp_path):
    llm = _HintSensitiveLLM()

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=1),
    )

    assert "composite_ref" in result
    assert llm.repair_hints == [None, EXPECTED_HINT]
    assert Path(result["composite_ref"]).exists()


async def test_never_good_repair_failure_is_bounded_and_writes_no_composite(tmp_path):
    llm = _HintSensitiveLLM(never_good=True)

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=0),
    )

    assert result["error"] == "planner_unmatched_segment"
    assert llm.repair_hints == [None]
    assert not (tmp_path / "composite.ts.md").exists()


async def test_default_baml_llm_receives_pipeline_config(tmp_path, monkeypatch):
    cfg = _cfg(max_repair_passes=0)
    constructed_with = []

    class _DefaultLLM(_HintSensitiveLLM):
        def __init__(self, *, cfg):
            super().__init__()
            constructed_with.append(cfg)

        async def arrange(self, candidates, strategy, *, repair_hint=None):
            self.repair_hints.append(repair_hint)
            return _blueprint("verbatim words")

    monkeypatch.setattr(pipeline_mod, "BamlPlannerLLM", _DefaultLLM)

    result = await pipeline_mod.plan(SRC, words=_words(), out_dir=tmp_path, cfg=cfg)

    assert "composite_ref" in result
    assert constructed_with == [cfg]
