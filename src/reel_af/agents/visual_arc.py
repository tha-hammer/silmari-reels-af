"""Visual Arc Planner — pre-assigns distinct (anchor_type, visual_trick)
across all scenes BEFORE the per-scene shot director runs in parallel.

Why: when parallel shot directors each independently pick anchor+visual_trick,
they tend to converge on whatever's most obvious for each line ("literal +
transformation" wins again and again). The result feels visually
homogeneous even if every individual shot is fine.

This single-pass planner sees ALL scenes at once and dictates the visual
arc: which scene gets the face-fill close-up, which gets the POV-hands,
which is the contrast shot. Then the per-scene directors only have to
WRITE the prompt that fits their pre-assigned slot — they don't have to
also pick the slot.

Trade: 1 extra cheap .ai() call upfront for guaranteed visual variety.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reel_af.agents.scene_breaker import Scene
from reel_af.agents.visual_vocab import VisualVocabulary, format_vocab_for_prompt

AnchorType = Literal["literal", "metaphor", "contrast"]
VisualTrickId = Literal[
    "face_fill", "pov_hands", "transformation", "scale_contrast",
    "movement_into_frame", "isolated_object", "human_scale_detail",
]


class SceneVisualPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_idx: int
    anchor_type: AnchorType
    visual_trick: VisualTrickId
    motif_id: str = Field(
        ...,
        description=(
            "The motif_id from the article's visual vocabulary that this shot "
            "draws from. Must match one of the provided vocabulary motifs exactly."
        ),
    )
    one_line_concept: str = Field(
        ...,
        description=(
            "ONE sentence sketching the shot — how the chosen motif is framed "
            "for THIS specific scene. The director will turn this into a full "
            "image+motion prompt."
        ),
    )


class VisualArc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plans: list[SceneVisualPlan] = Field(..., min_length=1)


def _build_system(
    vocab_block: str,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> str:
    from reel_af.agents.creator_playbook import (
        SCIENTIFIC_VISUAL_GUIDE,
        VISUAL_ACCESSIBILITY_GUIDE,
    )
    if content_mode == "scientific":
        mode_block = (
            f"\n\n{SCIENTIFIC_VISUAL_GUIDE}\n\n"
            f"MODE NOTE: this is a SCIENTIFIC paper. The visual arc must "
            f"stay in field-specific, technical-aesthetic territory across "
            f"every beat. Open on the artifact that makes the result "
            f"recognisable (the actual plot, the actual interface, the "
            f"actual system). Use motion to DEMONSTRATE behaviour, not to "
            f"add mood. No glowing brains. No abstract data-flow swirls.\n"
        )
    elif topic_familiarity == "obscure":
        mode_block = (
            f"\n\n{VISUAL_ACCESSIBILITY_GUIDE}\n\n"
            f"AUDIENCE NOTE: this article is OBSCURE. Your visual arc should "
            f"favour motifs that DEFINE the subject visually for a viewer with "
            f"no prior knowledge. The first 1-2 scenes should anchor on the "
            f"clearest possible visualisation of WHAT the topic is.\n"
        )
    else:
        mode_block = ""
    return _build_system_inner(vocab_block) + mode_block


def _build_system_inner(vocab_block: str) -> str:
    return f"""You are designing the VISUAL ARC for a vertical reel. You see
the full script as a list of scenes AND the article's specific visual
vocabulary. You assign each scene a distinct visual treatment that draws
from the vocabulary — never invent generic imagery.

You decide for EACH scene:
  1. anchor_type   — literal / metaphor / contrast
  2. visual_trick  — one from the menu below
  3. motif_id      — one from the article's vocabulary (verbatim)
  4. one_line_concept — how that motif is framed for this specific beat

THREE STRICT RULES:

(1) NO two consecutive scenes share the same visual_trick.
(2) Across ALL scenes, use AT LEAST 4 different visual_tricks.
(3) Across ALL scenes, use AT LEAST 3 different motifs from the vocabulary.
    A motif may repeat (different angle/framing) but never twice in a row.

VISUAL TRICKS MENU (each shot picks one — vary across scenes):
  face_fill           — face fills the frame, viewer can read emotion
  pov_hands           — first-person hands doing the thing
  transformation      — mid-action change: before → after, build → reveal
  scale_contrast      — dramatic size comparison in one frame
  movement_into_frame — subject enters/exits, peripheral motion catches eye
  isolated_object     — one object, cinematic light, neutral background
  human_scale_detail  — abstract concept shown next to hand/coin for scale

{vocab_block}

CREATIVE PRINCIPLES:
  • For each scene, pick the motif that MOST DIRECTLY anchors what's being
    said. If the line names a person/place/thing in the article, use the
    motif tied to that subject.
  • Pace the arc: open with something arresting. Pull back for the body.
    Land the close on a clean punctuation shot (isolated_object or face_fill).
  • Hooks (scene 0) earn the most attention-grabbing visual_trick.
  • Closes (last scene) deserve a clean motif that the viewer rewatches.

Return one SceneVisualPlan per input scene in order. Each must include a
valid motif_id from the vocabulary above (verbatim)."""


async def plan_visual_arc(
    app: Any,
    scenes: list[Scene],
    tone: str,
    full_script: str,
    vocab: VisualVocabulary,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> list[SceneVisualPlan]:
    listing = "\n".join(
        f"  [{s.idx}] role={s.role}  sentence={s.sentence!r}  caption={s.caption!r}"
        for s in scenes
    )
    user = (
        f"REEL TONE: {tone}\n"
        f"TOPIC FAMILIARITY: {topic_familiarity}\n"
        f"CONTENT MODE: {content_mode}\n\n"
        f"FULL SCRIPT (for context):\n{full_script}\n\n"
        f"SCENES TO PLAN VISUALS FOR:\n{listing}"
    )
    system = _build_system(
        format_vocab_for_prompt(vocab),
        topic_familiarity=topic_familiarity,
        content_mode=content_mode,
    )
    arc = await app.ai(system=system, user=user, schema=VisualArc)

    # Validate motif_ids against vocabulary; coerce any drift to the first
    # vocab motif so the downstream director doesn't blow up.
    valid_ids = {m.motif_id for m in vocab.motifs}
    fallback = vocab.motifs[0].motif_id
    plans = []
    for p in arc.plans:
        if p.motif_id not in valid_ids:
            p = p.model_copy(update={"motif_id": fallback})
        plans.append(p)
    return sorted(plans, key=lambda p: p.scene_idx)
