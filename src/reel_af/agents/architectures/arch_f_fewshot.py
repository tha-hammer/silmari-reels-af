"""Architecture F — Few-Shot Cloning of viral exemplars.

Bet: imitation beats invention for genre-bound content. Real high-performing
reels follow a small number of structural templates (Hormozi value-bombs,
Hank Green wonder explainers, Justin Welsh contrarian takes, etc.). Give
the model 3 close exemplars from this library and tell it to imitate the
STRUCTURE — not the surface words — and it should produce something
indistinguishable from the genre.

Stages:
  1. Direction picker — what kind of reel suits this article?
  2. Exemplar selector — pull 3 exemplars matching the direction
  3. Cloner — write the script imitating the exemplars' structure
  4. Single critic pass to catch any drift from the exemplars
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reel_af.agents.architectures import ArchOutput
from reel_af.agents.architectures.viral_exemplars import (
    EXEMPLARS,
    format_for_prompt,
    select_exemplars,
)
from reel_af.agents.creator_playbook import OBSCURE_WRITING_GUIDE, SCIENTIFIC_WRITING_GUIDE
from reel_af.agents.distiller import ArticleSummary
from reel_af.agents.reel_composer import Direction, ReelDraft


# ───── Step 1 — pick the direction that suits this article ──────────


class _DirectionPick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Direction
    why: str = Field(..., description="1 sentence: why this direction fits THIS article.")


_DIR_SYSTEM = """You are picking ONE presentation direction for a vertical reel
about an article. Match what the AUTHOR is doing in the source:

  explainer        — teach how something works
  discovery        — surprising fact / finding
  counterintuitive — flip a common belief
  tutorial         — numbered steps
  inspiration      — someone did X — you can too
  breakdown        — explain an event/situation

Pick the closest fit. The reel will be written by imitating exemplars from
that direction's library."""


async def _pick_direction(app: Any, summary: ArticleSummary) -> Direction:
    user = (
        f"ARTICLE\n"
        f"  domain   : {summary.domain}\n"
        f"  thesis   : {summary.one_line_thesis}\n"
        f"  takeaway : {summary.intended_takeaway}\n"
        f"  examples : {summary.concrete_examples}"
    )
    pick = await app.ai(system=_DIR_SYSTEM, user=user, schema=_DirectionPick)
    return pick.direction


# ───── Step 2-3 — clone the structure of exemplars ──────────────────


_CLONE_SYSTEM_TEMPLATE = """You are writing a vertical-reel script by
IMITATING the structure of proven viral exemplars. Below are 3 exemplars
chosen for your target article's direction.

MODE RULES (read first — three regimes, scientific takes precedence):

  • If `content_mode == "scientific"` (research paper / preprint / technical
    writeup): IGNORE the exemplars' creator-voice rhythm. Audience is
    engineers / dev-Twitter / scientifically-literate — use the field's
    jargon freely, lead with the result, name the method, cite the baseline.
    The exemplars are useful only for STRUCTURAL cues (where a payoff lands,
    how a close earns a save) — surface register comes from the scientific
    guide below, not from the exemplars.

    {SCIENTIFIC_GUIDE_PLACEHOLDER}

  • If `content_mode == "general"` and `topic_familiarity == "hot"`
    (mainstream / audience already knows): cold-open with a contrarian hook
    as the exemplars do. The exemplars are written in the contrarian-creator
    voice — match it.

  • If `content_mode == "general"` and `topic_familiarity == "obscure"`
    (niche product / specific researcher / inside-baseball / specialised
    research): IGNORE the exemplars' punchy cold-open style. Instead, follow
    the obscure-topic writing guide appended below. Plain language. Define
    before you judge. Conversational explainer register — like a friend
    telling you about something interesting they read, not a creator
    dunking on you.

    {OBSCURE_GUIDE_PLACEHOLDER}


Your job:
  • Read the exemplars. Notice the RHYTHM, SENTENCE LENGTHS, HOOK SHAPE,
    HOW THEY DELIVER PAYOFF, HOW THEY CLOSE.
  • Write a NEW script for the target article that matches the structural
    patterns — same rhythm, same kind of openings, same closing energy.
  • DO NOT copy any words from the exemplars. The exemplars are about
    different topics — you're writing about THIS article.
  • Use the article's actual examples, names, and numbers.

NARRATION RHYTHM (this is what makes Kokoro NOT sound robotic):
  • Wildly vary sentence length. SHORT. SHORT. Then a longer flowing one.
  • Use repetition for emphasis when the moment earns it: "They shut. It.
    Down." Three single-word periods = three deliberate beats.
  • Em-dashes (—) for ONE dramatic pause per script.
  • Commas where a human speaker would breathe.
  • ALL CAPS for ONE word maximum — only at the script's emotional peak.

CLOSE (the last 1-2 sentences must LAND):
  • End on a punch, not a fade. Last word is a noun or strong verb.
  • Match the exemplar's close shape (loop_closure / save_bait / cliffhanger
    / comment_bait / callback_punch).
  • NEVER end on ellipsis, hedge, or generic CTA.

Content rules (faithful):
  • Use the article's own examples, names, numbers — verbatim where possible.
  • Never invent metaphors not in the source.
  • Second person where natural.
  • No AI tells ("Did you know", "In this video", "Let's talk about").

Total: 55-68 words (~21-26s spoken). Tight is better.

{exemplars}

Pick your own hook_trick, retention_trick, close_trick, voice_tone, and
viral_score — but they MUST be consistent with the structural patterns
you chose to imitate. Return a ReelDraft."""


async def _clone(
    app: Any, summary: ArticleSummary, direction: Direction
) -> tuple[ReelDraft, list[str]]:
    exemplars = select_exemplars(direction, n=3)
    # Inject only the guide the current mode needs (keeps prompt lean).
    is_scientific = summary.content_mode == "scientific"
    is_obscure = (not is_scientific) and summary.topic_familiarity == "obscure"
    scientific_block = SCIENTIFIC_WRITING_GUIDE if is_scientific else ""
    obscure_block = OBSCURE_WRITING_GUIDE if is_obscure else ""
    system = _CLONE_SYSTEM_TEMPLATE.format(
        exemplars=format_for_prompt(exemplars),
        SCIENTIFIC_GUIDE_PLACEHOLDER=scientific_block,
        OBSCURE_GUIDE_PLACEHOLDER=obscure_block,
    )
    user = (
        f"TARGET ARTICLE SUMMARY (this is what your script is ABOUT — not the "
        f"exemplars):\n"
        f"  domain           : {summary.domain}\n"
        f"  content_mode     : {summary.content_mode}\n"
        f"  audience_level   : {summary.audience_level}\n"
        f"  topic_familiarity: {summary.topic_familiarity}\n"
        f"  thesis           : {summary.one_line_thesis}\n"
        f"  takeaway         : {summary.intended_takeaway}\n"
        f"  examples         : {summary.concrete_examples}\n"
        f"  key points:\n"
        + "\n".join(f"    {i+1}. {p}" for i, p in enumerate(summary.key_points))
    )
    draft = await app.ai(system=system, user=user, schema=ReelDraft)
    # Force-set the direction (the model sometimes overrides).
    draft = draft.model_copy(update={"direction": direction})
    used_ids = [e.id for e in exemplars]
    return draft, used_ids


# ───── Public entrypoint ────────────────────────────────────────────


async def run(app: Any, summary: ArticleSummary) -> ArchOutput:
    t0 = time.time()
    trace: list[str] = []

    direction = await _pick_direction(app, summary)
    trace.append(f"picked direction: {direction}")

    draft, used_exemplars = await _clone(app, summary, direction)
    trace.append(f"cloned from exemplars: {', '.join(used_exemplars)}")
    trace.append(f"self-score: {draft.viral_score}/10")

    return ArchOutput(
        arch_id="F",
        arch_name="Few-Shot Cloning of viral exemplars",
        bet="Imitation beats invention — viral content follows a small number of proven templates.",
        draft=draft,
        self_score=float(draft.viral_score),
        wall_time_s=time.time() - t0,
        trace=trace,
    )
