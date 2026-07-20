from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from baml_client.types import (
    Beat,
    BeatRole,
    CandidateSpan,
    CtaHardness,
    CtaPlan,
    DurationPolicy,
    EngagementKind,
    Hook,
    HookType,
    LoopPlan,
    PlannerCandidate,
    ReelBlueprint,
    ReelStrategy,
    Template,
)
from reel_af.planner import llm as llm_mod
from reel_af.planner.llm import FakePlannerLLM, NeverPlannerLLM, PlannerLLM
from tests.planner.factories import arc_plan, duration_policy, duration_range


@dataclass
class _BamlCall:
    name: str
    args: tuple[Any, ...]
    baml_options: dict[str, Any]


class _RecordingRegistry:
    def __init__(self) -> None:
        self.clients: list[dict[str, Any]] = []
        self.primary: str | None = None

    def add_llm_client(self, name: str, provider: str, options: dict[str, Any]) -> None:
        self.clients.append({"name": name, "provider": provider, "options": options})

    def set_primary(self, name: str) -> None:
        self.primary = name


class _RecordingBaml:
    def __init__(self) -> None:
        self.calls: list[_BamlCall] = []
        self.mine_result = [_candidate("verbatim words")]
        self.strategy_result = _strategy()
        self.arrange_result = _blueprint()

    async def MineCandidates(  # noqa: N802 - generated BAML API shape
        self,
        transcript_text: str,
        register: str,
        *,
        baml_options: dict[str, Any],
    ) -> list[CandidateSpan]:
        self.calls.append(_BamlCall("MineCandidates", (transcript_text, register), baml_options))
        return self.mine_result

    async def StrategizeReel(  # noqa: N802 - generated BAML API shape
        self,
        transcript_text: str,
        candidates: list[PlannerCandidate],
        policy: DurationPolicy,
        *,
        baml_options: dict[str, Any],
    ) -> ReelStrategy:
        self.calls.append(
            _BamlCall("StrategizeReel", (transcript_text, candidates, policy), baml_options)
        )
        return self.strategy_result

    async def ArrangeReel(  # noqa: N802 - generated BAML API shape
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        repair_hint: str | None = None,
        *,
        baml_options: dict[str, Any],
    ) -> ReelBlueprint:
        self.calls.append(
            _BamlCall("ArrangeReel", (candidates, strategy, repair_hint), baml_options)
        )
        return self.arrange_result


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-openrouter")
    return SimpleNamespace(
        model="anthropic/claude-sonnet-5",
        llm_temperature=0.25,
        llm_connect_timeout_s=1.5,
        llm_request_timeout_s=12.0,
        llm_total_timeout_s=30.0,
        max_transcript_chars=100,
        max_candidates=5,
    )


@pytest.fixture
def policy() -> DurationPolicy:
    return duration_policy(advisory_min_s=15.0, advisory_max_s=30.0)


@pytest.fixture
def planner_candidates() -> list[PlannerCandidate]:
    return [_planner_candidate("verbatim words")]


@pytest.fixture
def strategy() -> ReelStrategy:
    return _strategy()


def _patch_baml(monkeypatch: pytest.MonkeyPatch, rec: _RecordingBaml) -> None:
    monkeypatch.setattr(llm_mod, "_baml_functions", lambda: rec)
    monkeypatch.setattr(llm_mod, "_client_registry_type", lambda: _RecordingRegistry)


def _candidate(
    quote: str = "they pattern match",
    *,
    rationale: str | None = "strong mined span with clear hook/payoff potential",
) -> CandidateSpan:
    return CandidateSpan(
        quote=quote,
        approx_start_s=0.0,
        approx_end_s=1.0,
        value_score=0.9,
        emotion="surprise",
        is_claim=True,
        payoff_worthy=True,
        rationale=rationale,
    )


def _planner_candidate(
    quote: str = "they pattern match",
    *,
    rationale: str | None = "accepted verbatim span with clear hook/payoff potential",
) -> PlannerCandidate:
    return PlannerCandidate(
        candidate_id="c001",
        quote=quote,
        occurrence_index=0,
        word_range=[0, 2],
        start_s=0.0,
        end_s=1.0,
        quality=0.9,
        value_score=0.9,
        emotion="surprise",
        is_claim=True,
        payoff_worthy=True,
        rationale=rationale,
    )


def _hook(span_quote: str = "verbatim words") -> Hook:
    return Hook(
        type=HookType.CuriosityGap,
        banner_line="Verbatim words",
        span_quote=span_quote,
        candidate_id="c001",
        occurrence_index=0,
    )


def _strategy(
    *,
    range_min_s: float = 18.0,
    range_max_s: float = 28.0,
    required_candidate_ids: tuple[str, ...] = ("c001",),
    rationale: str | None = "the template, hook, arc, range, engagement, and CTA fit one thread",
) -> ReelStrategy:
    return ReelStrategy(
        **_template_field(ReelStrategy, Template.HookContextValuePayoffCta),
        duration_range_s=duration_range(min_s=range_min_s, max_s=range_max_s),
        duration_policy=duration_policy(advisory_min_s=15.0, advisory_max_s=30.0),
        arc=arc_plan(required_candidate_ids=required_candidate_ids),
        hook=_hook(),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        rationale=rationale,
    )


def _blueprint(
    *,
    range_min_s: float = 20.0,
    range_max_s: float = 35.0,
    required_candidate_ids: tuple[str, ...] = ("c001",),
    completion_rationale: str | None = "the hook, proof, payoff, and loop criteria are all covered",
    rationale: str | None = "the beat order builds to payoff and the loop echoes the hook",
) -> ReelBlueprint:
    return ReelBlueprint(
        **_template_field(ReelBlueprint, Template.HookContextValuePayoffCta),
        duration_range_s=duration_range(min_s=range_min_s, max_s=range_max_s),
        duration_policy=duration_policy(),
        arc=arc_plan(required_candidate_ids=required_candidate_ids),
        hook=_hook("they pattern match"),
        beats=[
            Beat(
                role=BeatRole.Hook,
                span_quote="they pattern match",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
            )
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote="they pattern match",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        completion_rationale=completion_rationale,
        rationale=rationale,
    )


def _template_field(model: type[Any], value: Template) -> dict[str, Template]:
    field = "template" if "template" in getattr(model, "model_fields", {}) else "template_"
    return {field: value}


async def test_fake_planner_llm_returns_baml_structs(policy: DurationPolicy):
    candidate = _candidate()
    strategy = _strategy()
    blueprint = _blueprint()
    fake: PlannerLLM = FakePlannerLLM(
        candidates=[candidate],
        strategy=strategy,
        blueprint=blueprint,
    )

    candidates = await fake.mine("transcript", "educational")
    planned = await fake.strategize("transcript", candidates, policy)
    arranged = await fake.arrange(candidates, planned)

    assert candidates == [candidate]
    assert planned is strategy
    assert arranged is blueprint
    assert fake.calls == [
        ("mine", "educational"),
        ("strategize", policy),
        ("arrange", None),
    ]


async def test_never_planner_llm_raises_if_called(strategy: ReelStrategy):
    llm = NeverPlannerLLM()

    with pytest.raises(AssertionError, match="must not be called"):
        await llm.arrange([], strategy)


async def test_mine_passes_client_registry_and_returns_baml_objects_directly(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
):
    rec = _RecordingBaml()
    _patch_baml(monkeypatch, rec)

    out = await llm_mod.BamlPlannerLLM(cfg=cfg).mine("verbatim words", "educational")

    assert rec.calls[0].name == "MineCandidates"
    assert rec.calls[0].args == ("verbatim words", "educational")
    registry = rec.calls[0].baml_options["client_registry"]
    assert registry.primary == "planner_runtime"
    assert registry.clients == [
        {
            "name": "planner_runtime",
            "provider": "openrouter",
            "options": {
                "model": "anthropic/claude-sonnet-5",
                "api_key": "sk-test-openrouter",
                "temperature": 0.25,
                "llm_connect_timeout_s": 1.5,
                "llm_request_timeout_s": 12.0,
                "llm_total_timeout_s": 30.0,
            },
        }
    ]
    assert out == rec.mine_result
    assert out[0] is rec.mine_result[0]


async def test_mine_enforces_transcript_and_candidate_limits(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
):
    rec = _RecordingBaml()
    _patch_baml(monkeypatch, rec)
    llm = llm_mod.BamlPlannerLLM(cfg=cfg)

    with pytest.raises(llm_mod.BamlPlannerInputError, match="max_transcript_chars"):
        await llm.mine("x" * 101, "educational")
    assert rec.calls == []

    rec.mine_result = [_candidate(f"candidate {i}") for i in range(6)]
    with pytest.raises(llm_mod.BamlPlannerContractError, match="max_candidates"):
        await llm.mine("short transcript", "educational")

    rec.mine_result = [_candidate("verbatim words", rationale=None)]
    with pytest.raises(llm_mod.BamlPlannerContractError, match="MineCandidates rationale"):
        await llm.mine("short transcript", "educational")


async def test_strategize_passes_baml_candidates_and_policy_without_dumping(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    policy: DurationPolicy,
):
    rec = _RecordingBaml()
    _patch_baml(monkeypatch, rec)

    out = await llm_mod.BamlPlannerLLM(cfg=cfg).strategize("t", planner_candidates, policy)

    assert rec.calls[0].name == "StrategizeReel"
    assert rec.calls[0].args[1] is planner_candidates
    assert rec.calls[0].args[1][0] is planner_candidates[0]
    assert rec.calls[0].args[2] is policy
    assert out is rec.strategy_result


async def test_strategize_rejects_missing_duration_policy_before_baml(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
):
    rec = _RecordingBaml()
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerInputError, match="duration policy"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).strategize("t", planner_candidates, None)

    assert rec.calls == []


async def test_strategize_rejects_invalid_duration_range(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    policy: DurationPolicy,
):
    rec = _RecordingBaml()
    rec.strategy_result = _strategy(range_min_s=40.0, range_max_s=30.0)
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="duration_range_s"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).strategize("t", planner_candidates, policy)


async def test_strategize_rejects_duration_range_above_effective_cap(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    policy: DurationPolicy,
):
    rec = _RecordingBaml()
    rec.strategy_result = _strategy(range_max_s=181.0)
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="effective_cap_s"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).strategize("t", planner_candidates, policy)


async def test_strategize_rejects_unknown_required_candidate(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    policy: DurationPolicy,
):
    rec = _RecordingBaml()
    rec.strategy_result = _strategy(required_candidate_ids=("missing",))
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="unknown"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).strategize("t", planner_candidates, policy)


async def test_strategize_rejects_missing_rationale(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    policy: DurationPolicy,
):
    rec = _RecordingBaml()
    rec.strategy_result = _strategy(rationale=None)
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="StrategizeReel rationale"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).strategize("t", planner_candidates, policy)


async def test_arrange_passes_strategy_object_without_dumping(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    strategy: ReelStrategy,
):
    rec = _RecordingBaml()
    _patch_baml(monkeypatch, rec)
    param = inspect.signature(llm_mod.BamlPlannerLLM.arrange).parameters["repair_hint"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY

    out = await llm_mod.BamlPlannerLLM(cfg=cfg).arrange(
        planner_candidates,
        strategy,
        repair_hint="fix candidate c001",
    )

    assert rec.calls[0].name == "ArrangeReel"
    assert rec.calls[0].args[0] is planner_candidates
    assert rec.calls[0].args[1] is strategy
    assert rec.calls[0].args[2] == "fix candidate c001"
    assert out is rec.arrange_result


async def test_arrange_rejects_missing_rationale(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    strategy: ReelStrategy,
):
    rec = _RecordingBaml()
    rec.arrange_result = _blueprint(rationale=None)
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="ArrangeReel rationale"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).arrange(planner_candidates, strategy)


async def test_arrange_rejects_missing_completion_rationale(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    strategy: ReelStrategy,
):
    rec = _RecordingBaml()
    rec.arrange_result = _blueprint(completion_rationale="")
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="completion_rationale"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).arrange(planner_candidates, strategy)


async def test_arrange_rejects_duration_range_above_strategy_cap(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
    strategy: ReelStrategy,
):
    rec = _RecordingBaml()
    rec.arrange_result = _blueprint(range_max_s=181.0)
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerContractError, match="effective_cap_s"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).arrange(planner_candidates, strategy)


async def test_fake_llm_records_repair_hint(strategy: ReelStrategy):
    fake = FakePlannerLLM(candidates=[], strategy=strategy, blueprint=_blueprint())

    await fake.arrange([], strategy, repair_hint="failed quote")

    assert fake.calls[-1] == ("arrange", "failed quote")


async def test_arrange_rejects_missing_strategy_before_baml(
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
    planner_candidates: list[PlannerCandidate],
):
    rec = _RecordingBaml()
    _patch_baml(monkeypatch, rec)

    with pytest.raises(llm_mod.BamlPlannerInputError, match="strategy"):
        await llm_mod.BamlPlannerLLM(cfg=cfg).arrange(planner_candidates, None)

    assert rec.calls == []
