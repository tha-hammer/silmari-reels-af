from __future__ import annotations

import json
from pathlib import Path

from reel_af import app as app_mod
from reel_af.app import dsl_hooks_to_reels
from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    DSL_HOOKS_WORKFLOW,
    CompileContext,
    DslWord,
    SourceRef,
    WordsSidecar,
    validate_renderable,
)
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
    PlannerCandidate,
    ReelBlueprint,
    ReelStrategy,
    ScriptCoherenceFixAction,
    ScriptCoherenceReport,
    ScriptTransitionReview,
    ScriptTransitionVerdict,
    Template,
    XfadeEffect,
)
from reel_af.planner.pipeline import (
    _cap_candidates_with_source_diversity,
    _transcript_windows,
    plan,
)
from tests.planner.factories import arc_plan, duration_policy, duration_range

SRC = "https://www.youtube.com/watch?v=abc123"
FIXTURES = Path(__file__).resolve().parents[1] / "dsl" / "fixtures"
SOURCE_QUOTE = (
    "They don't reason. They pattern-match at a scale that feels like reasoning. Right. "
    "And the moment you trust the feeling, you ship the bug. Anyway, "
    "So the fix isn't a smarter model. It's a tighter loop. "
    "A loop you can actually see closing."
)


class _FakePlannerLLM:
    def __init__(
        self,
        *blueprints: ReelBlueprint,
        coherence_reports: list[ScriptCoherenceReport] | None = None,
    ):
        self._blueprints = list(blueprints)
        self._coherence_reports = list(coherence_reports or [])
        self.mine_calls = 0
        self.strategize_calls = 0
        self.arrange_calls = 0
        self.coherence_calls = 0
        self.coherence_repair_hints: list[str | None] = []

    async def mine(self, transcript, register):
        self.mine_calls += 1
        return [
            CandidateSpan(
                quote=SOURCE_QUOTE,
                approx_start_s=4.12,
                approx_end_s=81.16,
                value_score=0.9,
                emotion="skepticism",
                is_claim=True,
                payoff_worthy=True,
                rationale="the full quote contains the hook, consequence, and payoff thread",
            )
        ]

    async def strategize(self, transcript, candidates, policy):
        self.strategize_calls += 1
        return _strategy()

    async def arrange(self, candidates, strategy, *, candidate_contexts=None, repair_hint=None):
        self.arrange_calls += 1
        idx = min(self.arrange_calls - 1, len(self._blueprints) - 1)
        return self._blueprints[idx]

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
        self.coherence_calls += 1
        self.coherence_repair_hints.append(repair_hint)
        if self._coherence_reports:
            idx = min(self.coherence_calls - 1, len(self._coherence_reports) - 1)
            return self._coherence_reports[idx]
        return _coherent_report(len(transitions))


def _blueprint(*, broken: bool = False) -> ReelBlueprint:
    first_quote = (
        "This quote is not present in the transcript"
        if broken
        else "They don't reason. They pattern-match at a scale that feels like reasoning."
    )
    return ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=18.0, max_s=42.0),
        duration_policy=duration_policy(advisory_min_s=15.0, advisory_max_s=45.0),
        arc=arc_plan(required_candidate_ids=("c001",)),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="They don't reason.",
            span_quote=first_quote,
            candidate_id="c001",
            occurrence_index=0,
        ),
        beats=[
            Beat(
                role=BeatRole.Hook,
                span_quote=first_quote,
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=4.5,
                rationale="the hook names the false reasoning premise before any mechanism appears",
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Dissolve,
                    dur_s=0.8,
                ),
            ),
            Beat(
                role=BeatRole.Value,
                span_quote="And the moment you trust the feeling, you ship the bug.",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=4.5,
                rationale="this beat supplies the consequence that makes the premise matter",
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Smoothleft,
                    dur_s=1.0,
                ),
            ),
            Beat(
                role=BeatRole.Payoff,
                span_quote="So the fix isn't a smarter model. It's a tighter loop.",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=4.5,
                rationale="this payoff answers the hook by naming the tighter-loop fix",
                interrupt_out=Interrupt(
                    kind=InterruptKind.Trans,
                    effect=XfadeEffect.Dissolve,
                    dur_s=0.6,
                ),
            ),
            Beat(
                role=BeatRole.Cta,
                span_quote="A loop you can actually see closing.",
                candidate_id="c001",
                occurrence_index=0,
                max_len_s=3.0,
                rationale="the final loop restates the visible loop idea without adding a new topic",
            ),
        ],
        loop=LoopPlan(
            strategy="tie_final_to_hook",
            final_span_quote="A loop you can actually see closing.",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        completion_rationale=(
            "The hook establishes the promise, the middle proof explains the mechanism, "
            "the payoff resolves the hook, and the final loop echoes the hook."
        ),
        rationale="the order moves from AI skepticism to the tighter-loop payoff and loop echo",
    )


def _strategy() -> ReelStrategy:
    return ReelStrategy(
        template_=Template.HookContextValuePayoffCta,
        duration_range_s=duration_range(min_s=18.0, max_s=42.0),
        duration_policy=duration_policy(advisory_min_s=15.0, advisory_max_s=45.0),
        arc=arc_plan(required_candidate_ids=("c001",)),
        hook=Hook(
            type=HookType.CuriosityGap,
            banner_line="They don't reason.",
            span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
            candidate_id="c001",
            occurrence_index=0,
        ),
        engagement_primary=EngagementKind.Send,
        cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
        rationale="the template uses one AI-process arc with enough latitude and a soft CTA",
    )


def _coherent_report(transition_count: int) -> ScriptCoherenceReport:
    return ScriptCoherenceReport(
        coherent=True,
        transitions=[
            ScriptTransitionReview(
                transition_index=index,
                from_beat_index=index,
                to_beat_index=index + 1,
                verdict=ScriptTransitionVerdict.Coherent,
                fix_action=ScriptCoherenceFixAction.Keep,
                why_present=True,
                rationale="the next beat follows because the prior beat sets up its consequence",
                missing_why=None,
                suggested_bridge_candidate_ids=[],
                suggested_repair=None,
            )
            for index in range(transition_count)
        ],
        overall_rationale="the assembled script keeps one local proof thread",
        repair_hint=None,
    )


def _seed_words():
    return load_words(FIXTURES / "source.words.json")


def _cfg(**overrides) -> PlannerConfig:
    data = load_planner_config().model_dump()
    data.update(overrides)
    return PlannerConfig.model_validate(data)


def _planner_candidate(
    candidate_id: str,
    *,
    source_window_index: int,
    value_score: float,
) -> PlannerCandidate:
    return PlannerCandidate(
        candidate_id=candidate_id,
        quote=f"{candidate_id} proof",
        occurrence_index=0,
        word_range=[0, 1],
        start_s=float(source_window_index * 600),
        end_s=float(source_window_index * 600 + 2),
        source_window_id=f"w{source_window_index:03d}",
        source_window_index=source_window_index,
        source_window_start_s=float(source_window_index * 600),
        source_window_end_s=float(source_window_index * 600 + 180),
        quality=1.0,
        value_score=value_score,
        rationale="accepted high-value proof span",
    )


def test_transcript_windows_cover_long_source_with_bounded_count():
    words = WordsSidecar(
        words=[
            DslWord(w="early", start=0.0, end=0.5),
            DslWord(w="setup", start=0.5, end=1.0),
            DslWord(w="middle", start=900.0, end=900.5),
            DslWord(w="proof", start=900.5, end=901.0),
            DslWord(w="late", start=1679.0, end=1679.5),
            DslWord(w="payoff", start=1679.5, end=1680.0),
        ]
    )
    cfg = _cfg(mine_window_duration_s=180.0, mine_window_overlap_s=15.0, mine_max_windows=12)

    windows = _transcript_windows(words, cfg)

    assert 1 < len(windows) <= cfg.mine_max_windows
    assert windows[0].start_s == 0.0
    assert windows[-1].end_s == 1680.0
    assert any("early setup" in window.text for window in windows)
    assert any("middle proof" in window.text for window in windows)
    assert any("late payoff" in window.text for window in windows)


def test_candidate_cap_preserves_source_window_diversity():
    cfg = _cfg(max_candidates=3, mine_candidates_per_window=1)
    candidates = [
        _planner_candidate("early", source_window_index=0, value_score=0.70),
        _planner_candidate("middle", source_window_index=1, value_score=0.99),
        _planner_candidate("middle-b", source_window_index=1, value_score=0.98),
        _planner_candidate("late", source_window_index=2, value_score=0.71),
    ]

    selected = _cap_candidates_with_source_diversity(candidates, cfg)

    assert [candidate.source_window_index for candidate in selected] == [0, 1, 2]


async def test_plan_promise_compiles_ok(tmp_path):
    words = _seed_words()
    res = await plan(
        SRC,
        words=words,
        register="educational",
        bounds={"min_s": 15, "max_s": 45},
        llm=_FakePlannerLLM(_blueprint()),
        out_dir=tmp_path,
    )

    text = Path(res["composite_ref"]).read_text(encoding="utf-8")
    doc = read_composite(text)
    out = compile_composite(
        doc,
        words,
        SourceRef(source_url=SRC),
        context=CompileContext(workflow=DSL_HOOKS_WORKFLOW, source_url=SRC),
    )

    assert out.status == "ok", out.diagnostics
    validate_renderable(out.plan)

    blueprint = json.loads(Path(res["blueprint_ref"]).read_text(encoding="utf-8"))
    strategy = json.loads(Path(res["strategy_ref"]).read_text(encoding="utf-8"))
    mined = json.loads(Path(res["mined_candidates_ref"]).read_text(encoding="utf-8"))
    accepted = json.loads(Path(res["accepted_candidates_ref"]).read_text(encoding="utf-8"))

    assert blueprint["rationale"]
    assert all(beat["rationale"] for beat in blueprint["beats"])
    assert strategy["rationale"]
    assert mined[0]["rationale"]
    assert accepted[0]["rationale"]
    coherence = json.loads(Path(res["script_coherence_ref"]).read_text(encoding="utf-8"))
    assert coherence["coherent"] is True
    assert len(coherence["transitions"]) == len(blueprint["beats"]) - 1


async def test_produced_triple_compiles_through_real_consumer(tmp_path, monkeypatch):
    fetched_segments = []

    def _fake_fetch_segment(request):
        fetched_segments.append(request.segment_id)
        request.target_path.write_bytes(b"not-real-video")
        return request.target_path

    async def _fake_stitch_footage_reel(*args, **kwargs):
        path = tmp_path / "base.mp4"
        path.write_bytes(b"not-real-video")
        return path

    async def _fake_finish_reel(*args, **kwargs):
        path = tmp_path / "final.mp4"
        path.write_bytes(b"not-real-video")
        return path

    monkeypatch.setattr(app_mod, "stitch_footage_reel", _fake_stitch_footage_reel)
    monkeypatch.setattr(app_mod, "finish_reel", _fake_finish_reel)

    res = await plan(
        SRC,
        words=_seed_words(),
        register="educational",
        bounds={"min_s": 15, "max_s": 45},
        llm=_FakePlannerLLM(_blueprint()),
        out_dir=tmp_path / "producer",
    )

    out = await dsl_hooks_to_reels(
        SRC,
        res["composite_ref"],
        res["words_ref"],
        res["hook_ref"],
        clip_idx=1,
        out_dir=str(tmp_path / "consume"),
        fetch_segment=_fake_fetch_segment,
        uploader=lambda *args, **kwargs: "https://bucket.example.com/reel.mp4",
        text_provider=object(),
        image_provider=object(),
        artifact_fetch=lambda ref: Path(ref).read_bytes(),
    )

    assert out.get("error") != "dsl_compile_failed", out
    assert out["target_workflow"] == DSL_HOOKS_WORKFLOW
    assert fetched_segments


async def test_below_floor_then_good_compiles(tmp_path):
    llm = _FakePlannerLLM(_blueprint(broken=True), _blueprint())

    res = await plan(
        SRC,
        words=_seed_words(),
        register="educational",
        bounds={"min_s": 15, "max_s": 45},
        llm=llm,
        out_dir=tmp_path,
    )

    assert "composite_ref" in res
    assert llm.arrange_calls == 2


async def test_never_good_fails_typed_no_composite(tmp_path):
    res = await plan(
        SRC,
        words=_seed_words(),
        register="educational",
        bounds={"min_s": 15, "max_s": 45},
        llm=_FakePlannerLLM(_blueprint(broken=True)),
        out_dir=tmp_path,
    )

    assert res["error"] == "planner_unmatched_segment"
    assert res["diagnostics"]
    assert {d["code"] for d in res["diagnostics"]} <= {
        "UNMATCHED_SEGMENT",
        "CANDIDATE_NOT_FOUND",
    }
    assert not (tmp_path / "composite.ts.md").exists()


async def test_missing_composite_ref_in_hook_plan_breaks_real_consumer(tmp_path):
    res = await plan(
        SRC,
        words=_seed_words(),
        register="educational",
        bounds={"min_s": 15, "max_s": 45},
        llm=_FakePlannerLLM(_blueprint()),
        out_dir=tmp_path,
    )
    hook_path = Path(res["hook_ref"])
    hook_plan = json.loads(hook_path.read_text(encoding="utf-8"))
    hook_plan["clips"][0].pop("composite_ref", None)
    hook_path.write_text(json.dumps(hook_plan), encoding="utf-8")

    out = await dsl_hooks_to_reels(
        SRC,
        res["composite_ref"],
        res["words_ref"],
        res["hook_ref"],
        clip_idx=1,
        out_dir=str(tmp_path / "consume"),
        fetch_segment=lambda req: req.target_path,
        uploader=lambda *args, **kwargs: "https://bucket.example.com/reel.mp4",
        artifact_fetch=lambda ref: Path(ref).read_bytes(),
    )

    assert out["error"] == "dsl_artifact_unavailable"
