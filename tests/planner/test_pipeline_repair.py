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
    Interrupt,
    InterruptKind,
    LoopPlan,
    ReelBlueprint,
    ReelStrategy,
    ScriptCoherenceFixAction,
    ScriptCoherenceReport,
    ScriptTransitionReview,
    ScriptTransitionVerdict,
    Template,
    XfadeEffect,
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


def _join_words() -> WordsSidecar:
    return WordsSidecar(
        words=[
            DslWord(w="earlier", start=0.0, end=0.5),
            DslWord(w="source", start=0.5, end=1.0),
            DslWord(w="clip", start=1.0, end=1.5),
            DslWord(w="later", start=30.0, end=30.5),
            DslWord(w="source", start=30.5, end=31.0),
            DslWord(w="clip", start=31.0, end=31.5),
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
        rationale="the strategy follows the local repair thread",
    )


def _join_strategy() -> ReelStrategy:
    return ReelStrategy(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=4.0, max_s=8.0),
        duration_policy=duration_policy(),
        arc=arc_plan(
            required_candidate_ids=("c001", "c002"),
            completion_criteria=(
                "hook establishes the later source promise",
                "payoff resolves the earlier source contrast",
                "loop echoes the hook from a distinct span",
            ),
            promise="the later source promise resolves only if the cut is legal",
            thread="one local thread with a deliberately invalid join first",
        ),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="Later source",
            span_quote="later source clip",
            candidate_id="c002",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        rationale="the strategy is intentionally small so render compile owns join legality",
    )


def _blueprint(
    quote: str,
    final_quote: str = "fix works",
    template: Template = Template.HookContextValuePayoffCta,
    loop_candidate: str = "c001",
) -> ReelBlueprint:
    # AF-9zs: R8 is a mandatory error, satisfied here by the loop closing on the
    # hook candidate (loop_candidate == hook c001). Pass a foreign
    # loop_candidate + non-echoing final_quote to model an R8 miss.
    return ReelBlueprint(
        template_=template,
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
                rationale="the hook is the earliest clean statement in the repair thread",
            ),
            Beat(
                role=BeatRole.Payoff,
                span_quote=final_quote,
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
                rationale="the payoff supplies the missing fix promised by the hook",
            )
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote=final_quote,
            candidate_id=loop_candidate,
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        completion_rationale="hook establishes the promise and payoff resolves the hook",
        rationale="the arranged beats move from the verbatim hook to its fix",
    )


def _join_blueprint(*, use_join: bool) -> ReelBlueprint:
    return ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=4.0, max_s=8.0),
        duration_policy=duration_policy(),
        arc=_join_strategy().arc,
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="Later source",
            span_quote="later source clip",
            candidate_id="c002",
            occurrence_index=0,
        ),
        beats=[
            Beat(
                role=BeatRole.Hook,
                span_quote="later source clip",
                candidate_id="c002",
                occurrence_index=0,
                max_len_s=2.0,
                rationale=(
                    "the hook establishes the later source promise before the payoff contrast"
                ),
                interrupt_out=Interrupt(kind=InterruptKind.Join)
                if use_join
                else Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Dissolve,
                    dur_s=0.3,
                ),
            ),
            Beat(
                role=BeatRole.Payoff,
                span_quote="earlier source clip",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=2.0,
                rationale="the payoff resolves the earlier source contrast after a visible cut",
            ),
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote="earlier source clip",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        completion_rationale=(
            "hook establishes the later source promise; payoff resolves the earlier source "
            "contrast; loop echoes the hook from a distinct span"
        ),
        rationale="the script is coherent only when the non-forward boundary is a cut, not a join",
    )


class _HintSensitiveLLM:
    def __init__(
        self,
        *,
        never_good: bool = False,
        initial_quote: str = "paraphrase",
        coherence_reports: list[ScriptCoherenceReport] | None = None,
    ) -> None:
        self.never_good = never_good
        self.initial_quote = initial_quote
        self.repair_hints: list[str | None] = []
        self.coherence_reports = list(coherence_reports or [])
        self.coherence_hints: list[str | None] = []

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
                rationale="a compact span containing the repair hook and fix",
            )
        ]

    async def strategize(self, transcript, candidates, policy):
        return _strategy()

    async def arrange(self, candidates, strategy, *, candidate_contexts=None, repair_hint=None):
        self.repair_hints.append(repair_hint)
        if not self.never_good and (
            repair_hint == EXPECTED_HINT
            or (repair_hint is not None and "SCRIPT-COHERENCE" in repair_hint)
        ):
            return _blueprint("verbatim words")
        return _blueprint(self.initial_quote)

    async def check_script_coherence(
        self,
        blueprint,
        script_beats,
        transitions,
        strategy,
        candidate_contexts,
        *,
        repair_hint=None,
    ):
        self.coherence_hints.append(repair_hint)
        if self.coherence_reports:
            return self.coherence_reports.pop(0)
        return _coherence_report(coherent=True, transition_count=len(transitions))


class _JoinRefusedLLM:
    def __init__(self) -> None:
        self.repair_hints: list[str | None] = []

    async def mine(self, transcript, register):
        return [
            CandidateSpan(
                quote="earlier source clip",
                approx_start_s=0.0,
                approx_end_s=1.5,
                value_score=0.9,
                emotion="plain",
                is_claim=False,
                payoff_worthy=True,
                rationale="earlier candidate used to make the join non-forward",
            ),
            CandidateSpan(
                quote="later source clip",
                approx_start_s=30.0,
                approx_end_s=31.5,
                value_score=0.9,
                emotion="plain",
                is_claim=True,
                payoff_worthy=True,
                rationale="later candidate used as the hook side of the boundary",
            ),
        ]

    async def strategize(self, transcript, candidates, policy):
        return _join_strategy()

    async def arrange(self, candidates, strategy, *, candidate_contexts=None, repair_hint=None):
        self.repair_hints.append(repair_hint)
        return _join_blueprint(use_join=not (repair_hint and "JOIN_REFUSED" in repair_hint))

    async def check_script_coherence(
        self,
        blueprint,
        script_beats,
        transitions,
        strategy,
        candidate_contexts,
        *,
        repair_hint=None,
    ):
        return _coherence_report(coherent=True, transition_count=len(transitions))


def _coherence_report(*, coherent: bool, transition_count: int = 1) -> ScriptCoherenceReport:
    verdict = (
        ScriptTransitionVerdict.Coherent if coherent else ScriptTransitionVerdict.UnbridgedJump
    )
    fix_action = ScriptCoherenceFixAction.Keep if coherent else ScriptCoherenceFixAction.Bridge
    return ScriptCoherenceReport(
        coherent=coherent,
        transitions=[
            ScriptTransitionReview(
                transition_index=index,
                from_beat_index=index,
                to_beat_index=index + 1,
                verdict=verdict,
                fix_action=fix_action,
                why_present=coherent,
                rationale=(
                    "the next line follows from the prior one"
                    if coherent
                    else "the fix appears without the connective why from the source"
                ),
                missing_why=None if coherent else "the reason the fix follows the hook",
                suggested_bridge_candidate_ids=[] if coherent else ["ctx_c001_after"],
                suggested_repair=None if coherent else "include the connective after c001",
            )
            for index in range(transition_count)
        ],
        overall_rationale=(
            "the assembled script reads coherently"
            if coherent
            else "the assembled script drops the connective why"
        ),
        repair_hint=None if coherent else "bridge ctx_c001_after before the payoff",
    )


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


class _R8HintSensitiveLLM(_HintSensitiveLLM):
    """AF-9zs: arranges a loop that misses the R8 echo until an R8 repair hint
    arrives; ``never_good`` keeps missing it (terminal enforcement path)."""

    async def arrange(self, candidates, strategy, *, candidate_contexts=None, repair_hint=None):
        self.repair_hints.append(repair_hint)
        if not self.never_good and repair_hint is not None and "R8" in repair_hint:
            return _blueprint("verbatim words")            # hook-candidate loop → passes
        return _blueprint(
            "verbatim words",
            final_quote="fix works",                       # no hook token echo...
            loop_candidate="c002",                         # ...and a foreign loop → R8 miss
            template=Template.ProblemAgitateSolve,         # the eval's failing case
        )


async def test_r8_miss_triggers_loop_echo_repair_then_compiles(tmp_path):
    """AF-9zs: a missing loop tie-back is repairable — the second arrange pass
    receives a hint naming the mandatory R8 echo and the reel plans."""
    llm = _R8HintSensitiveLLM()

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=1),
    )

    assert "composite_ref" in result
    assert llm.repair_hints[0] is None
    assert "R8" in llm.repair_hints[1]
    assert "echo" in llm.repair_hints[1].lower()


async def test_r8_never_echoing_is_terminal_retention_lint_failed(tmp_path):
    """AF-9zs: with repairs exhausted a PAS reel that never echoes its hook can
    no longer ship — terminal retention_lint_failed carrying the R8 error."""
    llm = _R8HintSensitiveLLM(never_good=True)

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=1),
    )

    assert result["error"] == "retention_lint_failed"
    assert any(
        diag["rule"] == "R8" and diag["severity"] == "error"
        for diag in result["diagnostics"]
    )
    assert not (tmp_path / "composite.ts.md").exists()


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


async def test_script_coherence_repair_reprompts_arrange_and_persists_report(tmp_path):
    llm = _HintSensitiveLLM(
        initial_quote="verbatim words",
        coherence_reports=[
            _coherence_report(coherent=False),
            _coherence_report(coherent=True),
        ]
    )

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=2),
    )

    assert "composite_ref" in result
    assert llm.repair_hints[0] is None
    assert "SCRIPT-COHERENCE" in (llm.repair_hints[1] or "")
    assert llm.coherence_hints == [None, llm.repair_hints[1]]
    report = Path(result["script_coherence_ref"]).read_text(encoding="utf-8")
    assert '"coherent": true' in report


async def test_script_coherence_allows_two_bounded_repair_passes(tmp_path):
    llm = _HintSensitiveLLM(
        initial_quote="verbatim words",
        coherence_reports=[
            _coherence_report(coherent=False),
            _coherence_report(coherent=False),
            _coherence_report(coherent=True),
        ],
    )

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=1),
    )

    assert "composite_ref" in result
    assert len(llm.repair_hints) == 3
    assert all("SCRIPT-COHERENCE" in (hint or "") for hint in llm.repair_hints[1:])
    assert llm.coherence_hints == llm.repair_hints


async def test_script_coherence_failure_returns_typed_diagnostic(tmp_path):
    llm = _HintSensitiveLLM(
        initial_quote="verbatim words",
        coherence_reports=[_coherence_report(coherent=False)],
    )

    result = await plan(
        SRC,
        words=_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=0),
    )

    assert result["error"] == pipeline_mod.PLANNER_SCRIPT_COHERENCE_FAILED
    assert result["diagnostics"][0]["code"] == "SCRIPT_COHERENCE_FAILED"
    assert not (tmp_path / "composite.ts.md").exists()


async def test_render_compile_join_refusal_reprompts_arrange_with_cut(tmp_path):
    llm = _JoinRefusedLLM()

    result = await plan(
        SRC,
        words=_join_words(),
        llm=llm,
        out_dir=tmp_path,
        cfg=_cfg(max_repair_passes=1, mine_candidates_per_window=2, max_candidates=2),
    )

    assert "composite_ref" in result
    assert llm.repair_hints[0] is None
    assert "JOIN_REFUSED" in (llm.repair_hints[1] or "")
    composite = Path(result["composite_ref"]).read_text(encoding="utf-8")
    assert "[join]" not in composite
    assert "[trans dissolve 0.3]" in composite


async def test_default_baml_llm_receives_pipeline_config(tmp_path, monkeypatch):
    cfg = _cfg(max_repair_passes=0)
    constructed_with = []

    class _DefaultLLM(_HintSensitiveLLM):
        def __init__(self, *, cfg):
            super().__init__()
            constructed_with.append(cfg)

        async def arrange(self, candidates, strategy, *, candidate_contexts=None, repair_hint=None):
            self.repair_hints.append(repair_hint)
            return _blueprint("verbatim words")

    monkeypatch.setattr(pipeline_mod, "BamlPlannerLLM", _DefaultLLM)

    result = await pipeline_mod.plan(SRC, words=_words(), out_dir=tmp_path, cfg=cfg)

    assert "composite_ref" in result
    assert constructed_with == [cfg]
