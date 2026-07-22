"""AF-vjm — topic reel must not hard-fail when the judge-winning narration
misses the ScriptDraft loop-back gate.

Fix shape (issue option A + scoped C safety net):
  1. candidate fallback: try narration candidates in judge order, first one
     whose mapped ScriptDraft validates wins;
  2. if none validate, relax the gate for the winner via
     ``ScriptDraft.enforce_loop_back=False`` so the reel still renders.

The opt-out must be invisible to the article LLM schema (compose_script
passes ``schema=ScriptDraft`` to ``app.ai``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from reel_af.models import ScriptDraft

TEASE = "Why are bananas curved?"

# Last 12 words contain neither "bananas" nor "curved".
NARRATION_MISSES_LOOP = (
    "Why are bananas curved? Fruit chases light as it grows on the plant. "
    "Auxin shifts to the shaded side and cells there stretch faster. "
    "And that is the entire secret of how plants grow toward light."
)
# Ends on the hook keywords → passes the gate.
NARRATION_LOOPS_BACK = (
    "Why are bananas curved? Fruit chases light as it grows on the plant. "
    "Auxin shifts to the shaded side and cells there stretch faster. "
    "And that reach for sunlight is exactly why bananas are curved."
)


def _draft_payload(**overrides) -> dict:
    payload = {
        "hook": TEASE,
        "hook_variant": "curiosity_gap",
        "mechanism_lines": [
            "Fruit chases light as it grows on the plant.",
            "Auxin shifts to the shaded side and cells there stretch faster.",
        ],
        "payoff_line": "That reach for sunlight is why bananas are curved.",
        "target_wpm": 180,
        "narration": NARRATION_MISSES_LOOP,
    }
    payload.update(overrides)
    return payload


def _conv_script(narration: str, common_belief: str | None = None) -> dict:
    """A ConversationalScript-shaped dict as produced by model_dump()."""
    return {
        "tease": TEASE,
        "common_belief": common_belief,
        "reveal": (
            "Fruit chases light as it grows on the plant. "
            "Auxin shifts to the shaded side and cells there stretch faster."
        ),
        "payoff": "That reach for sunlight is why bananas are curved.",
        "open_style": "question",
        "target_wpm": 180,
        "narration": narration,
    }


def _essences(n: int = 3) -> list[dict]:
    return [
        {
            "core_claim": f"claim {i}",
            "mechanism": f"mechanism {i}",
            "evidence": [f"evidence {i}a", f"evidence {i}b"],
            "domain": "botany",
        }
        for i in range(n)
    ]


# ── Behavior 1-3: the gate itself ───────────────────────────────────


def test_loop_back_gate_strict_by_default():
    """Article-path semantics unchanged: a miss still raises."""
    with pytest.raises(ValidationError, match="Loop-back missing"):
        ScriptDraft(**_draft_payload())


def test_enforce_loop_back_false_skips_gate():
    draft = ScriptDraft(**_draft_payload(enforce_loop_back=False))
    assert draft.narration == NARRATION_MISSES_LOOP


def test_enforce_loop_back_hidden_from_llm_schema():
    """compose_script sends ScriptDraft as the LLM schema; the opt-out must
    not be offered to the model."""
    props = ScriptDraft.model_json_schema()["properties"]
    assert "enforce_loop_back" not in props


# ── Behavior 4-6: candidate selection helper ─────────────────────────


def test_winner_passing_gate_is_selected():
    from reel_af.app import select_topic_script

    scripts = [
        _conv_script(NARRATION_MISSES_LOOP),
        _conv_script(NARRATION_LOOPS_BACK),
        _conv_script(NARRATION_MISSES_LOOP),
    ]
    script, essence, idx, relaxed = select_topic_script(
        scripts, _essences(), winner_idx=1,
    )
    assert idx == 1
    assert relaxed is False
    assert script["hook"] == TEASE
    assert essence["core_claim"] == "claim 1"
    assert essence["content_mode"] == "general"
    ScriptDraft(**script)  # must construct downstream (plan_beats)


def test_failing_winner_falls_back_to_next_candidate_in_judge_order():
    from reel_af.app import select_topic_script

    scripts = [
        _conv_script(NARRATION_LOOPS_BACK),
        _conv_script(NARRATION_MISSES_LOOP),
        _conv_script(NARRATION_MISSES_LOOP),
    ]
    script, essence, idx, relaxed = select_topic_script(
        scripts, _essences(), winner_idx=1,
    )
    assert idx == 0
    assert relaxed is False
    assert script["narration"] == NARRATION_LOOPS_BACK
    assert essence["core_claim"] == "claim 0"
    ScriptDraft(**script)


def test_all_candidates_failing_relaxes_winner_never_crashes():
    from reel_af.app import select_topic_script

    scripts = [_conv_script(NARRATION_MISSES_LOOP) for _ in range(3)]
    script, essence, idx, relaxed = select_topic_script(
        scripts, _essences(), winner_idx=2,
    )
    assert idx == 2
    assert relaxed is True
    assert script["enforce_loop_back"] is False
    assert essence["core_claim"] == "claim 2"
    ScriptDraft(**script)  # relaxed draft must construct downstream


# ── Behavior 7: ConversationalScript → ScriptDraft mapping shape ─────


def test_mapping_pads_single_sentence_reveal_with_common_belief():
    from reel_af.app import select_topic_script

    conv = _conv_script(NARRATION_LOOPS_BACK, common_belief="Most assume gravity.")
    conv["reveal"] = "Fruit chases light as it grows on the plant"  # 1 sentence
    script, _, _, _ = select_topic_script([conv], _essences(1), winner_idx=0)
    assert script["hook_variant"] == "curiosity_gap"
    assert script["target_wpm"] == 180
    assert script["payoff_line"] == conv["payoff"]
    assert script["narration"] == NARRATION_LOOPS_BACK
    assert len(script["mechanism_lines"]) == 2
    assert script["mechanism_lines"][1] == "Most assume gravity."


# ── Behavior 8: wiring closure ───────────────────────────────────────


def test_topic_to_reel_calls_select_topic_script():
    import reel_af.app as app_mod

    source = Path(app_mod.__file__).read_text()
    _, _, after = source.partition("async def topic_to_reel(")
    body = after.split("\n@reel.reasoner()")[0]
    assert "select_topic_script(" in body
