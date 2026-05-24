"""Shot Director v2 — per-scene visual prompt with anchor_type and
visual_trick PRE-ASSIGNED by the visual arc planner.

The director's job here is narrower (and stronger): write the most
inventive possible image+motion prompt that fits its pre-assigned slot.
Because variety is handled upstream, this prompt can push the model
HARDER on creativity within constraints.

Context strategy:
  IN  : scene + full_script + tone + pre-assigned (anchor_type, visual_trick)
        + one_line_concept from visual arc planner
  OUT : { anchor_type (echoed), visual_trick (echoed), image_prompt, motion_prompt }
  NOT : article body (irrelevant), other scenes' plans (avoided contamination)

Runs in parallel via asyncio.gather — variety is already locked, so parallel
is safe.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reel_af.agents.creator_playbook import (
    SCIENTIFIC_VISUAL_GUIDE,
    VISUAL_ACCESSIBILITY_GUIDE,
    VISUAL_TRICKS,
)
from reel_af.agents.scene_breaker import Scene
from reel_af.agents.visual_arc import SceneVisualPlan, plan_visual_arc
from reel_af.agents.visual_vocab import VisualVocabulary

AnchorType = Literal["literal", "metaphor", "contrast"]

VisualTrickId = Literal[
    "face_fill", "pov_hands", "transformation", "scale_contrast",
    "movement_into_frame", "isolated_object", "human_scale_detail",
]


class ShotPlanV2(BaseModel):
    """Visual plan for one scene. Anchor+trick pre-assigned by visual_arc."""

    model_config = ConfigDict(extra="forbid")

    anchor_type: AnchorType = Field(
        ..., description="Echoed from the pre-assigned visual arc."
    )
    visual_trick: VisualTrickId = Field(
        ..., description="Echoed from the pre-assigned visual arc."
    )
    image_prompt: str = Field(
        ...,
        description=(
            "Single cinematic image prompt for grok-imagine. MUST visibly execute "
            "the assigned visual_trick. ONE concrete scene. Real subject and real "
            "environment. Specific framing (close-up / mid / wide), specific "
            "lighting. Vertical 9:16 framing. NO text/letters in frame. NO "
            "abstract concept art. The shot must contain ONE unexpected element "
            "(unusual angle / object out of place / surprising color / dramatic "
            "light) that catches the eye on the first frame."
        ),
    )
    motion_prompt: str = Field(
        ...,
        description=(
            "What should MOVE in the 4-second video. Be specific about subject "
            "AND camera. The motion must REVEAL or TRANSFORM something the still "
            "didn't show — never just 'slight camera push'. Pair the motion with "
            "what was promised by the image (transformation = something completes; "
            "movement_into_frame = subject enters; face_fill = micro-expression "
            "shifts). Avoid camera moves bigger than a slow push or gentle pan."
        ),
    )


def _build_system(
    tone: str,
    full_script: str,
    anchor: AnchorType,
    trick: VisualTrickId,
    concept: str,
    motif_description: str,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> str:
    trick_desc = VISUAL_TRICKS[trick]
    if content_mode == "scientific":
        mode_block = (
            f"\n\n{SCIENTIFIC_VISUAL_GUIDE}\n\n"
            f"MODE NOTE: this is a SCIENTIFIC paper. The shot must stay in "
            f"field-specific, technical-aesthetic territory. Real artifacts "
            f"(actual interfaces, actual instruments, actual plots, actual "
            f"systems) over mood. If the visual could illustrate ANY paper "
            f"in this field, it's wrong — make it specific to THIS paper. "
            f"Motion should DEMONSTRATE the mechanism or progress, not add "
            f"cinematic flourish.\n"
        )
    elif topic_familiarity == "obscure":
        mode_block = (
            f"\n\n{VISUAL_ACCESSIBILITY_GUIDE}\n\n"
            f"AUDIENCE NOTE: this article is OBSCURE. Apply the guide above — "
            f"your image MUST help DEFINE the subject for a viewer with zero "
            f"prior knowledge. Recognisable / specific / explanatory beats "
            f"cinematic-but-vague.\n"
        )
    else:
        mode_block = ""
    return f"""You design ONE shot of a vertical reel. The structural choices —
anchor type, visual trick, AND which article-specific motif to anchor on —
are LOCKED IN by the visual arc:

  anchor_type    = {anchor}
  visual_trick   = {trick}

VISUAL TRICK YOU MUST EXECUTE:
{trick_desc}

ARTICLE MOTIF YOU MUST GROUND ON (THIS is what the shot is about — do not
substitute a generic mood image):
{motif_description}

ROUGH CONCEPT FOR THIS SHOT (from the arc planner — feel free to elevate,
never contradict):
  {concept}

YOUR JOB: write the IMAGE PROMPT and MOTION PROMPT.

CREATIVE RULES (this is where reels die or pop):

(1) AVOID THE FIRST OBVIOUS THING.
    If the line says "phone", don't write a phone shot. Write the hand
    reaching for it. If it says "discovery", don't write a lightbulb. Write
    the scientist's face in the second AFTER they see something. The
    "second obvious" choice is where the eye stops.

(2) ONE UNEXPECTED ELEMENT.
    Every image must contain ONE thing the viewer doesn't expect — unusual
    framing, object out of place, color contrast, off-center subject,
    dramatic single-source light. This is what stops the scroll in 1 frame.

(3) MOTION REVEALS, NEVER RESTATES.
    The motion prompt must add information the still didn't show. Bad: "slow
    camera push on the man." Good: "the man's hand opens to reveal a folded
    paper he's been holding." The motion must do narrative work.

(4) REAL SUBJECTS, REAL ENVIRONMENTS.
    Documentary / cinematic feel. No abstract concept art ("glowing brain",
    "data flowing as light"). No stock-photo handshakes / generic offices.
    Pick a SPECIFIC place: a kitchen sink at 6am, a tarmac under a sodium
    light, a desk with one open notebook.

(5) PAIR IMAGE AND MOTION.
    They're not two independent choices. Design the still as the FROZEN
    moment of the motion. "Hand holding paper" image + "paper unfolds and a
    drawing is revealed" motion — that's a designed shot.

REEL TONE: {tone}.
{mode_block}
FULL SCRIPT (cohesion only — pick visuals distinct from other beats):
{full_script}

Return:
  anchor_type = {anchor}    (echo verbatim)
  visual_trick = {trick}    (echo verbatim)
  image_prompt              (one paragraph; cinematic, specific, vertical)
  motion_prompt             (one sentence; specific subject motion + subtle camera)
"""


async def _direct_one(
    app: Any,
    scene: Scene,
    plan: SceneVisualPlan,
    tone: str,
    full_script: str,
    motif_description: str,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> ShotPlanV2:
    system = _build_system(
        tone=tone,
        full_script=full_script,
        anchor=plan.anchor_type,
        trick=plan.visual_trick,
        concept=plan.one_line_concept,
        motif_description=motif_description,
        topic_familiarity=topic_familiarity,
        content_mode=content_mode,
    )
    user = (
        f"VO LINE FOR THIS SHOT:\n  {scene.sentence!r}\n\n"
        f"CAPTION (don't restate — design visuals that complement it):\n"
        f"  {scene.caption!r}\n\n"
        f"Role in arc: {scene.role}\n"
        f"Duration: ~{scene.est_duration_s:.1f}s"
    )
    out = await app.ai(system=system, user=user, schema=ShotPlanV2)
    # Force-preserve the assigned anchor/trick (model occasionally drifts).
    return out.model_copy(update={
        "anchor_type": plan.anchor_type,
        "visual_trick": plan.visual_trick,
    })


async def direct_shots_v2(
    app: Any,
    scenes: list[Scene],
    tone: str,
    full_script: str,
    vocab: VisualVocabulary,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> list[ShotPlanV2]:
    """Plan the visual arc once (vocabulary-grounded + familiarity-aware),
    then run per-scene directors in parallel with their assigned motif."""
    arc = await plan_visual_arc(
        app, scenes, tone, full_script, vocab,
        topic_familiarity=topic_familiarity,
        content_mode=content_mode,
    )
    arc_by_idx = {p.scene_idx: p for p in arc}
    motif_by_id = {m.motif_id: m for m in vocab.motifs}

    async def _one(scene: Scene) -> ShotPlanV2:
        plan = arc_by_idx.get(
            scene.idx,
            SceneVisualPlan(
                scene_idx=scene.idx,
                anchor_type="literal",
                visual_trick="isolated_object",
                motif_id=vocab.motifs[0].motif_id,
                one_line_concept=scene.sentence,
            ),
        )
        motif = motif_by_id.get(plan.motif_id, vocab.motifs[0])
        return await _direct_one(
            app, scene, plan, tone, full_script,
            motif_description=motif.description,
            topic_familiarity=topic_familiarity,
            content_mode=content_mode,
        )

    return list(await asyncio.gather(*(_one(s) for s in scenes)))
