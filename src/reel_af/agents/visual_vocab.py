"""Visual Vocabulary — article-specific visual motifs the reel can DRAW from.

The problem this solves: shot directors writing in isolation produce
generic mood imagery (lamps, desks, hands holding keys) because they have
no shared visual language for the specific article. The visuals end up
looking random — disconnected from "the NTSB" or "UPS Flight 2976" or
"spectrograms" even though those are exactly what the article is about.

Fix: ONE upfront call generates 6-8 concrete visual motifs specific to
this article. The visual arc planner picks from these motifs; the shot
director's image prompts must use one. Now every shot ANCHORS to
something article-specific instead of inventing yet another moody desk.

Context strategy:
  IN  : ArticleSummary (thesis + concrete examples)
  OUT : VisualVocabulary — list of {id, motif, when_to_use}
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reel_af.agents.distiller import ArticleSummary

MIN_MOTIFS = 6
MAX_MOTIFS = 8


class VisualMotif(BaseModel):
    """One concrete visual element specific to the article."""

    model_config = ConfigDict(extra="forbid")

    motif_id: str = Field(
        ...,
        description=(
            "Short snake_case tag. Examples: ntsb_hearing, ups_cargo_plane, "
            "spectrogram_screen, cockpit_recorder, ai_typing_codex, "
            "darwin_field_notes, calder_mobile."
        ),
    )
    description: str = Field(
        ...,
        description=(
            "1-2 sentences describing the motif in cinematic terms. Subject, "
            "environment, lighting, framing. Concrete enough that grok-imagine "
            "can render it consistently. NO text or letters."
        ),
    )
    when_to_use: str = Field(
        ...,
        description=(
            "1 sentence: what kind of script beat this motif fits — e.g. 'use "
            "for the hook because the cargo plane is visually arresting', or "
            "'use for the close because the locked cabinet is a punctuation shot'."
        ),
    )


class VisualVocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motifs: list[VisualMotif] = Field(..., min_length=MIN_MOTIFS, max_length=MAX_MOTIFS)


def _build_system(topic_familiarity: str, content_mode: str = "general") -> str:
    from reel_af.agents.creator_playbook import (
        SCIENTIFIC_VISUAL_GUIDE,
        VISUAL_ACCESSIBILITY_GUIDE,
    )
    # Scientific mode takes precedence — different audience, different visual
    # register. Engineers/technical-public want field artifacts, real systems,
    # data over moodboards.
    if content_mode == "scientific":
        mode_block = (
            f"\n\nMODE NOTE — content_mode = scientific\n\n"
            f"{SCIENTIFIC_VISUAL_GUIDE}\n"
            f"This is a scientific paper. Every motif you design must be "
            f"FIELD-SPECIFIC and CONCRETE — the actual artifacts, instruments, "
            f"interfaces, plots, or systems described in the paper. No mood "
            f"shots, no generic-tech aesthetic. If a motif could illustrate "
            f"ANY paper in this field, it's wrong. Design for someone who "
            f"would recognise these objects.\n"
        )
    elif topic_familiarity == "obscure":
        mode_block = (
            f"\n\nAUDIENCE NOTE — topic_familiarity = {topic_familiarity}\n\n"
            f"{VISUAL_ACCESSIBILITY_GUIDE}\n"
            f"This article is OBSCURE for the audience — every motif you "
            f"design must HELP DEFINE the subject visually, not just evoke a "
            f"mood. Prefer motifs that include the actual interface / object / "
            f"phenomenon over atmospheric setting shots.\n"
        )
    else:
        mode_block = (
            f"\nThis article is HOT for the audience — they already have a "
            f"referent. Cinematic mood shots are fine; you have more visual "
            f"latitude.\n"
        )
    return _SYSTEM_BASE + mode_block


_SYSTEM_BASE = f"""You're building a VISUAL VOCABULARY for a vertical reel about
an article. The shot director will pick from this vocabulary for each scene
instead of inventing generic imagery. Your job: produce {MIN_MOTIFS}-{MAX_MOTIFS}
CONCRETE, ARTICLE-SPECIFIC visual motifs.

What makes a good motif:
  • Tied to a real subject in the article (a person, a place, an object, an
    event — NOT an abstract metaphor).
  • Cinematic and renderable as a single 9:16 vertical still. Real-world.
  • Distinct from the others — different subjects, different scales.
  • Each motif should be USABLE multiple times across the reel (different
    angle, different moment) — these are vocabulary, not single shots.

EXAMPLES of what good vocabulary looks like:

For an article on octopi tasting with their suckers:
  - octopus_sucker_macro: extreme close-up of a single octopus sucker on
    a wet rock, water droplets, low underwater light. Use for revelation shots.
  - marine_biologist_field: a scientist in a wetsuit kneeling at a tide pool,
    notebook in hand, focused. Use to ground the discovery.
  - tasting_human_hand_contrast: a hand reaching toward an octopus that's
    extending its arm; close framing emphasises the contact. Use for the
    "your hand is missing something" stakes beat.

For an article on AI cloning dead pilots' voices:
  - ntsb_archive_files: rows of beige cardboard accident-investigation
    folders on metal shelves under fluorescent light. Use for hook + close.
  - ups_cockpit_dim: empty cockpit of a parked cargo plane at dusk, two
    seats facing forward, instruments dim. Use for the reveal.
  - spectrogram_on_monitor: a CRT-feeling monitor displaying a sound
    spectrogram waveform, dark room, faint operator silhouette. Use for
    the mechanism beat.
  - youtuber_workspace: a content creator's home setup: multi-monitor,
    headphones, an aviation poster on the wall. Use for the Scott Manley
    discovery beat.

What does NOT work:
  • "A hand holding a key" — too generic.
  • "A desk under a lamp" — too generic.
  • "A glowing brain" — abstract concept art.
  • "AI rewriting reality" — concept, not image.

Use the article's actual people, places, things, and numbers from the
summary's concrete examples list. If the article mentions Scott Manley,
your vocabulary should include a Scott-Manley-shaped shot. If it mentions
1844 and Darwin, include a 19th-century desk with field notebooks.

Be specific. Be cinematic. Be tied to THIS article."""


async def build_vocabulary(app: Any, summary: ArticleSummary) -> VisualVocabulary:
    """Generate the article-specific visual vocabulary. One .ai call."""
    user = (
        f"ARTICLE\n"
        f"  domain           : {summary.domain}\n"
        f"  content_mode     : {summary.content_mode}\n"
        f"  topic_familiarity: {summary.topic_familiarity}\n"
        f"  thesis           : {summary.one_line_thesis}\n"
        f"  takeaway         : {summary.intended_takeaway}\n\n"
        f"  key points:\n"
        + "\n".join(f"    {i+1}. {p}" for i, p in enumerate(summary.key_points))
        + "\n\n  concrete examples (people / places / things / numbers — these are "
        "your raw material for motifs):\n"
        + (
            "\n".join(f"    - {e}" for e in summary.concrete_examples)
            if summary.concrete_examples
            else "    (none — design abstract-but-article-specific motifs)"
        )
    )
    system = _build_system(
        topic_familiarity=summary.topic_familiarity,
        content_mode=summary.content_mode,
    )
    return await app.ai(system=system, user=user, schema=VisualVocabulary)


def format_vocab_for_prompt(vocab: VisualVocabulary) -> str:
    """Render the vocabulary as a structured menu the director can pick from."""
    lines = ["=== THIS ARTICLE'S VISUAL VOCABULARY (pick from these for every shot) ==="]
    for m in vocab.motifs:
        lines.append(f"\n[{m.motif_id}]")
        lines.append(f"  description : {m.description}")
        lines.append(f"  when_to_use : {m.when_to_use}")
    return "\n".join(lines)
