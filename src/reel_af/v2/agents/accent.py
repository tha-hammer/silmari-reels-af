"""Layer 2 editorial accent overlay per shot — one `.ai()` call, biased to None."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from reel_af.v2.models import AccentOverlay, Essence, Shot

# The default MUST be None. Every part of the prompt and schema is engineered
# to make the model commit to a yes/no BEFORE constructing the overlay so it
# can't be carried by overlay-construction momentum into emitting one.


class _AccentDecision(BaseModel):
    """Internal wrapper: forces the model to commit yes/no before designing."""

    model_config = ConfigDict(extra="forbid")

    emit_overlay: bool = Field(
        ...,
        description=(
            "Decide FIRST. Default is false. Only set true if one of the 6 "
            "canonical patterns unambiguously applies to this shot."
        ),
    )
    overlay: Optional[AccentOverlay] = Field(
        default=None,
        description=(
            "ONLY set when emit_overlay is true. Must be null otherwise. "
            "When set, text is 2-6 words; the renderer uppercases it."
        ),
    )
    reasoning: str = Field(
        ...,
        description=(
            "1-2 sentences: which of the 6 patterns this matches and why — "
            "OR why no pattern applies and the shot stays clean. Audit trail."
        ),
    )


_SYSTEM_PROMPT = (
    "You decide whether ONE shot of a vertical reel gets an editorial accent "
    "overlay on top of the verbatim word-by-word subtitles already burned at "
    "the upper-center of the frame.\n\n"
    "THE DEFAULT IS emit_overlay=false. Most shots will not emit. Repeat: "
    "most shots will not emit. Over-cluttering the frame is worse than not "
    "adding accent. If no pattern unambiguously applies, emit_overlay MUST "
    "be false. Do not manufacture overlays to fill a quota.\n\n"
    "Only six canonical patterns are allowed. Concrete examples:\n"
    "  • number — narration mentions a specific number worth locking in for a "
    "muted viewer (\"$47,000\", \"85%\", \"3 STEPS\"). The number must actually "
    "appear in this shot's narration.\n"
    "  • named_entity — a person, place, organization, or product whose "
    "spelling matters (\"DR. CHEN, STANFORD\", \"THE HIPPOCAMPUS\"). Not "
    "generic nouns.\n"
    "  • jargon_translation — the narration uses a domain term and a plain-"
    "English gloss would help muted viewers (\"ENTANGLEMENT = SPOOKY LINK\").\n"
    "  • hook_title_card — ONLY allowed when shot.role == 'hook'. A bold "
    "question or claim, 5-8 words, that becomes the title card.\n"
    "  • reaction — comedic punctuation on a spoken beat (\"WAIT WHAT\", "
    "\"NO WAY\", \"BIG IF TRUE\"). Only when the script clearly has that beat.\n"
    "  • list_marker — only when the script is structured as a numbered "
    "listicle (\"STEP 2 OF 3\", \"1 OF 3\"). Not for generic enumeration.\n\n"
    "Hard rules:\n"
    "  1. If this shot's narration doesn't contain a number to call out, a "
    "named entity that needs spelling, a jargon term being defined, or a "
    "clear emotional / structural beat — emit_overlay MUST be false.\n"
    "  2. hook_title_card is ONLY valid when shot.role == 'hook'. For any "
    "non-hook shot, never use this pattern.\n"
    "  3. Overlay text must be 2-6 words. The renderer uppercases it; you can "
    "write any case.\n"
    "  4. position: hook_title_card → 'upper_third'. Everything else → "
    "'lower_third' (opposite of the subtitles).\n"
    "  5. The overlay must surface information the voiceover does not "
    "explicitly say but the viewer needs to lock in (the number's exact "
    "value, the name's spelling, the comedic beat the audio can't show).\n\n"
    "Output a structured decision: pick emit_overlay first, then either set "
    "overlay to a valid AccentOverlay or leave it null. reasoning explains "
    "which pattern matched, or why none did."
)


def _join_shot_text(shot: Shot) -> str:
    """Reconstruct the spoken text of this shot from its subtitle cards."""
    return " ".join(card.text for card in shot.cards).strip()


def _build_user_prompt(shot: Shot, essence: Essence) -> str:
    evidence = "\n".join(f"  - {e}" for e in essence.evidence)
    return (
        f"SHOT INDEX: {shot.idx}\n"
        f"SHOT ROLE: {shot.role}\n"
        f"SHOT DURATION: {shot.duration_s:.2f}s\n"
        f"SHOT NARRATION (verbatim of what's spoken):\n"
        f"  {_join_shot_text(shot)!r}\n\n"
        f"CONTENT MODE: {essence.content_mode}\n"
        f"CORE CLAIM: {essence.core_claim}\n"
        f"EVIDENCE:\n{evidence}\n\n"
        f"Decide: does this shot warrant a Layer 2 editorial accent overlay?\n"
        f"Reminder: most shots should return emit_overlay=false."
    )


async def accent_for_shot(
    app: Any,
    shot: Shot,
    essence: Essence,
) -> AccentOverlay | None:
    """Single `.ai()` call. Returns None by default — only emits an overlay
    when one of the 6 canonical patterns is genuinely warranted."""

    decision = await app.ai(
        system=_SYSTEM_PROMPT,
        user=_build_user_prompt(shot, essence),
        schema=_AccentDecision,
    )

    if not decision.emit_overlay or decision.overlay is None:
        return None

    overlay = decision.overlay

    # Enforce the hook_title_card → role=='hook' restriction defensively.
    # If the model selected hook_title_card on a non-hook shot, drop the
    # overlay rather than ship a frame-stealing title card mid-mechanism.
    if overlay.pattern == "hook_title_card" and shot.role != "hook":
        return None

    # Enforce position rule: hook_title_card lives upper_third, everything
    # else opposite the subtitles (lower_third). The schema already defaults
    # to lower_third; correct the upper-third case so it's consistent.
    if overlay.pattern == "hook_title_card":
        overlay = overlay.model_copy(update={"position": "upper_third"})
    elif overlay.position == "upper_third":
        overlay = overlay.model_copy(update={"position": "lower_third"})

    return overlay


async def plan_accents(
    app: Any,
    shots: list[Shot],
    essence: Essence,
) -> list[AccentOverlay | None]:
    """Fan-out via `asyncio.gather`. Returns list aligned to `shots` by index."""
    if not shots:
        return []
    return list(
        await asyncio.gather(
            *(accent_for_shot(app, shot, essence) for shot in shots)
        )
    )
