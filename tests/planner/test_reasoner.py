from __future__ import annotations

from pathlib import Path

from reel_af import app as app_mod
from reel_af.app import transcript_to_plan
from reel_af.dsl.compile import load_words
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

SRC = "https://www.youtube.com/watch?v=abc123"
FIXTURES = Path(__file__).resolve().parents[1] / "dsl" / "fixtures"
SOURCE_QUOTE = (
    "They don't reason. They pattern-match at a scale that feels like reasoning. Right. "
    "And the moment you trust the feeling, you ship the bug. Anyway, "
    "So the fix isn't a smarter model. It's a tighter loop. "
    "A loop you can actually see closing."
)


class _FakePlannerLLM:
    async def mine(self, transcript, register):
        return [
            CandidateSpan(
                quote=SOURCE_QUOTE,
                approx_start_s=4.12,
                approx_end_s=81.16,
                value_score=0.9,
                emotion="skepticism",
                is_claim=True,
                payoff_worthy=True,
            )
        ]

    async def strategize(self, transcript, candidates, bounds):
        return None

    async def arrange(self, candidates, strategy, repair_hint=None):
        return ReelBlueprint(
            template_=Template.HookContextValuePayoffCta,
            target_duration_s=24.0,
            hook=Hook(
                type=HookType.CuriosityGap,
                banner_line="They don't reason.",
                span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
                candidate_id="c001",
                occurrence_index=0,
            ),
            beats=[
                Beat(
                    role=BeatRole.Hook,
                    span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
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
                    role=BeatRole.Payoff,
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


def _fake_transcribe(source):
    return load_words(FIXTURES / "source.words.json")


async def test_transcript_to_plan_returns_triple(tmp_path):
    out = await transcript_to_plan(
        SRC,
        register="educational",
        llm=_FakePlannerLLM(),
        transcribe=_fake_transcribe,
        out_dir=str(tmp_path),
    )

    assert set(out) >= {"composite_ref", "words_ref", "hook_ref"}


async def test_transcript_to_plan_rejects_non_http():
    out = await transcript_to_plan("file:///etc/passwd", llm=None, transcribe=None)

    assert out["error"] == "invalid_source_url"


def test_transcript_to_plan_is_registered_on_served_app():
    assert "reel_transcript_to_plan" in app_mod.app._reasoner_registry
