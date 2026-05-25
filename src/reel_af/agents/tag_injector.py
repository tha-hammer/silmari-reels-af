"""Tag Injector — adds Gemini Flash TTS audio tags to the narration script.

Gemini 3.1 Flash TTS accepts inline `[tag]` directives that the model
treats as STAGE DIRECTIONS — it never speaks them aloud. Tags steer
emotion, pacing, and emphasis with much higher fidelity than the
em-dash + ALL-CAPS pseudo-direction we used to bolt onto the script.

The injector runs AFTER the script writer (arch_b/arch_f) and BEFORE
the TTS call. It returns the same script with tags inserted at the
right beats. The original word sequence is preserved verbatim — only
bracketed tags are added.

Tag vocabulary (Gemini supports 200+; this is the working subset we
expose to the model). Categories:
  • Emotion     : [excited] [curious] [serious] [warm] [thoughtful]
                  [confident] [playful] [intense]
  • Pacing      : [pause] [pause short] [pause long] [breath]
                  [slow] [fast] [building]
  • Volume / weight: [whispers] [quiet] [emphasis] [loud]
  • Sentiment   : [wonder] [surprise] [skeptical] [hopeful]

The model is free to use any tag from Gemini's wider set if it fits
better — these are hints, not a closed enumeration.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _TaggedScript(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_with_tags: str = Field(
        ...,
        description=(
            "The same script as input, with Gemini TTS audio tags inserted "
            "inline. EVERY spoken word from the original must appear in the "
            "output, in the same order, with the same punctuation. Tags are "
            "the ONLY addition. Tags go in [square brackets]; place them "
            "BEFORE the clause they modify."
        ),
    )


_SYSTEM = """You add inline Gemini Flash TTS audio tags to a narration
script. The tags are STAGE DIRECTIONS — Gemini treats them as performance
hints and never speaks them aloud.

EXAMPLES OF WHAT GOOD TAGGING LOOKS LIKE:

input:
  "GPT-4o on MATH? Beaten by pure RL. No human-labeled reasoning chains —
   just verifiable outcomes. Pure RL just took GPT-4o's MATH crown."

output:
  "[curious] GPT-4o on MATH? [excited] Beaten by pure RL. [pause short]
   No human-labeled reasoning chains — [emphasis] just verifiable outcomes.
   [pause] [slow] Pure RL just took GPT-4o's MATH crown."

input:
  "84% of IMO geometry problems. Solved. AlphaGeometry2 just hit
   gold-medalist level."

output:
  "[wonder] 84% of IMO geometry problems. [pause short] [confident] Solved.
   [excited] AlphaGeometry2 just hit gold-medalist level."

TAGS TO USE (these are HINTS — Gemini knows 200+, you can use others that
fit better):

  Emotion           : [excited] [curious] [serious] [warm] [thoughtful]
                      [confident] [playful] [intense] [hopeful]
  Pacing            : [pause] [pause short] [pause long] [breath]
                      [slow] [fast] [building]
  Volume / weight   : [whispers] [quiet] [emphasis] [loud]
  Sentiment         : [wonder] [surprise] [skeptical]

RULES (non-negotiable):

  1. EVERY word from the input must appear in the output in the SAME
     ORDER and with the SAME PUNCTUATION. You ONLY add bracketed tags.
     Adding, removing, or reordering words is forbidden.

  2. Tags go BEFORE the clause they modify, not after. They are cues for
     the next phrase, not annotations of the previous one.

  3. Be deliberate. 4-8 tags per script is plenty. Tag every beat that
     shifts emotion or pace — don't tag every sentence.

  4. Open with a tag that sets the cold-start register (usually
     [curious], [excited], or [wonder] for a science reel).

  5. The LAST sentence should have a [slow] or [pause] before it so the
     close lands instead of trailing off.

  6. When a sentence delivers a specific NUMBER or NAMED RESULT, prefix
     it with [emphasis] or [building] so the model gives it weight.

  7. Em-dashes ARE preserved verbatim — Gemini handles them naturally
     when paired with [pause] or [pause short].

You are not rewriting. You are stage-directing."""


async def inject_tags(app: Any, script: str) -> str:
    """Return the script with Gemini TTS audio tags inserted inline."""
    user = f"SCRIPT TO TAG (preserve every word verbatim):\n\n{script.strip()}"
    out = await app.ai(system=_SYSTEM, user=user, schema=_TaggedScript)
    tagged = out.script_with_tags.strip()
    # Sanity check: if the model dropped words, fall back to the original.
    # Compare word counts after stripping bracketed tags.
    import re
    plain_tagged = re.sub(r"\[[^\]]+\]", "", tagged)
    if len(plain_tagged.split()) < int(len(script.split()) * 0.85):
        # Model lost too many words — return original untagged.
        return script
    return tagged
