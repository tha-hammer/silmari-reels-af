from __future__ import annotations

from pathlib import Path

from reel_af import app as app_mod
from reel_af.app import transcript_to_plan
from reel_af.dsl.compile import load_words
from reel_af.planner.models import Beat, CtaPlan, Hook, Interrupt, LoopPlan, ReelBlueprint

SRC = "https://www.youtube.com/watch?v=abc123"
FIXTURES = Path(__file__).resolve().parents[1] / "dsl" / "fixtures"


class _FakePlannerLLM:
    async def mine(self, transcript, register):
        return []

    async def strategize(self, transcript, candidates, bounds):
        return None

    async def arrange(self, candidates, strategy):
        return ReelBlueprint(
            template="hook_context_value_payoff_cta",
            target_duration_s=24.0,
            hook=Hook(
                type="curiosity_gap",
                banner_line="They don't reason.",
                span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
            ),
            beats=[
                Beat(
                    role="hook",
                    span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
                    max_len_s=4.5,
                    interrupt_out=Interrupt(kind="trans", effect="dissolve", dur_s=0.8),
                ),
                Beat(
                    role="payoff",
                    span_quote="A loop you can actually see closing.",
                    max_len_s=3.0,
                ),
            ],
            loop=LoopPlan(
                strategy="tie_final_to_hook",
                final_span_quote="A loop you can actually see closing.",
            ),
            engagement_primary="send",
            cta=CtaPlan(hardness="soft", placements=["end"]),
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
