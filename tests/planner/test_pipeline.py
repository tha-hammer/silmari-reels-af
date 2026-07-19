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
    SourceRef,
    validate_renderable,
)
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
    Template,
    XfadeEffect,
)
from reel_af.planner.pipeline import plan

SRC = "https://www.youtube.com/watch?v=abc123"
FIXTURES = Path(__file__).resolve().parents[1] / "dsl" / "fixtures"


class _FakePlannerLLM:
    def __init__(self, *blueprints: ReelBlueprint):
        self._blueprints = list(blueprints)
        self.mine_calls = 0
        self.strategize_calls = 0
        self.arrange_calls = 0

    async def mine(self, transcript, register):
        self.mine_calls += 1
        return [
            CandidateSpan(
                quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
                approx_start_s=4.12,
                approx_end_s=7.9,
                value_score=0.9,
                emotion="skepticism",
                is_claim=True,
                payoff_worthy=True,
            )
        ]

    async def strategize(self, transcript, candidates, bounds):
        self.strategize_calls += 1
        return None

    async def arrange(self, candidates, strategy, repair_hint=None):
        self.arrange_calls += 1
        idx = min(self.arrange_calls - 1, len(self._blueprints) - 1)
        return self._blueprints[idx]


def _blueprint(*, broken: bool = False) -> ReelBlueprint:
    first_quote = (
        "This quote is not present in the transcript"
        if broken
        else "They don't reason. They pattern-match at a scale that feels like reasoning."
    )
    return ReelBlueprint(
        template_=Template.HookContextValuePayoffCta,
        target_duration_s=24.0,
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
    )


def _seed_words():
    return load_words(FIXTURES / "source.words.json")


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
