"""Captioner — rewrites the per-scene burned text to be CONTRAPUNTAL.

Default scene_breaker captions are paraphrases of the spoken sentence —
they duplicate what the viewer already hears. The captioner runs after
scene splitting with the full article summary in context and rewrites
each caption so it ADDS information rather than echoing the voice.

The principles (embedded in the prompt) are derived from what works on
high-retention science reels: caption shows the number, the named term,
the punchline, or the plain-English translation of jargon the voice
just used.

Context strategy:
  IN  : ArticleSummary (for jargon glossary + thesis), full script,
        per-scene sentences (already split by scene_breaker).
  OUT : revised per-scene captions (same count, same order).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reel_af.agents.distiller import ArticleSummary
from reel_af.agents.scene_breaker import Scene


class _CaptionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_idx: int
    caption: str = Field(
        ...,
        description=(
            "1-4 words burned on-screen for this scene. NORMAL CASE (assembly "
            "uppercases). Punctuation only if it carries weight (the question "
            "mark in 'WAIT, REALLY?')."
        ),
    )


class _CaptionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captions: list[_CaptionEntry] = Field(..., min_length=1)


_SYSTEM = """You write on-screen captions for a vertical-reel narration.

A caption is NOT a subtitle. It is a SECOND READABLE CHANNEL of
information that COMPLEMENTS the spoken line. The viewer reads it in one
glance while their ears process the audio.

Pick exactly ONE of these moves per caption — pick the one that maximises
information density for the line:

  • NUMBER PUNCH      — pull out the specific number / percentage / year
                        the voice is saying or is about to say.
                        Voice: "84% of IMO geometry problems. Solved."
                        Caption: "84%"

  • NAMED RESULT      — the named method / model / phenomenon the line
                        introduces. Helps the viewer save the LABEL.
                        Voice: "...AlphaGeometry2 just hit gold-medalist level..."
                        Caption: "ALPHAGEOMETRY 2"

  • JARGON TRANSLATION — when the voice uses field jargon (e.g.
                        "RLHF", "surface code", "MMLU"), put the
                        plain-English equivalent on screen. Use the
                        article's jargon glossary as your source.
                        Voice: "...below the surface code threshold..."
                        Caption: "ERRORS BELOW BREAK-EVEN"

  • CONTRAST           — the unstated opposite the line is implicitly
                        rejecting. The voice says X; the caption says
                        "NOT Y" or names what just got broken.
                        Voice: "pure RL, zero human data, beats GPT-4o"
                        Caption: "NO HUMAN EXAMPLES"

  • PUNCHLINE EARLY    — the close beat. The caption shows the
                        consequence half-a-beat before the voice gets
                        there.
                        Voice: "Reasoning data: optional."
                        Caption: "DATA → OPTIONAL"

NEVER allowed:
  • Repeating the voice's words verbatim. If the line says "70% win rate"
    and your caption is "70% WIN RATE", you've duplicated information.
  • Generic filler: "INTERESTING", "WATCH THIS", "WOW", "HUGE".
  • Captions longer than 4 words (anything longer fails on a phone screen
    in 500ms).
  • Captions that ADD a claim the article doesn't support.

You receive: the article summary (thesis, intended takeaway, jargon
glossary), the full script, and the per-scene sentences. Return one
caption per scene, in the same order, indexed by scene_idx.

Pace yourself across scenes. Don't use the same MOVE every scene — vary:
hook scene gets a NUMBER PUNCH or NAMED RESULT; mid scenes can do
TRANSLATION or CONTRAST; close gets PUNCHLINE EARLY."""


async def rewrite_captions(
    app: Any,
    scenes: list[Scene],
    summary: ArticleSummary,
    full_script: str,
) -> list[Scene]:
    """Return new Scene list with rewritten contrapuntal captions."""
    glossary_lines = "\n".join(
        f"  - {entry.term} → {entry.plain}"
        for entry in summary.jargon_glossary[:12]
    ) or "  (no specialist terms)"
    scene_lines = "\n".join(
        f"  [{s.idx}] role={s.role:10s} sentence={s.sentence!r}"
        for s in scenes
    )
    user = (
        f"ARTICLE SUMMARY\n"
        f"  one_line_thesis : {summary.one_line_thesis}\n"
        f"  intended_takeaway: {summary.intended_takeaway}\n"
        f"  jargon glossary:\n{glossary_lines}\n\n"
        f"FULL NARRATION:\n{full_script}\n\n"
        f"PER-SCENE LINES:\n{scene_lines}"
    )
    batch = await app.ai(system=_SYSTEM, user=user, schema=_CaptionBatch)
    by_idx = {c.scene_idx: c.caption.strip() for c in batch.captions}
    result: list[Scene] = []
    for s in scenes:
        new_cap = by_idx.get(s.idx, s.caption).strip()
        # Fall back to original if the model returned empty / too long.
        if not new_cap or len(new_cap.split()) > 5:
            new_cap = s.caption
        result.append(
            Scene(
                idx=s.idx,
                sentence=s.sentence,
                caption=new_cap,
                est_duration_s=s.est_duration_s,
                role=s.role,
            )
        )
    return result
