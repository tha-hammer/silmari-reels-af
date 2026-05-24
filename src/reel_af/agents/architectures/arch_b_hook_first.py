"""Architecture B — Hook-First Cascade.

Bet: the hook is 80% of the success on vertical scroll. So optimize it in
ISOLATION first (8 candidates, all just the opening 5-7 words), pick the
strongest 2, and write the body of the script as a PAYOFF to the winning
hook. The body is constrained to deliver what the hook promises.

Why this might beat the pool approach: in a pool architecture each drafter
writes the entire script in one breath, so the hook has to compete with the
body for the model's attention budget. Here the model spends 100% of its
first-pass attention on hooks alone.

Stages:
  1. Generate 8 distinct hooks (parallel)
  2. Rank → pick top 2
  3. For each top hook, write a body that pays off + closes (parallel)
  4. Pick the better full script
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reel_af.agents.architectures import ArchOutput
from reel_af.agents.creator_playbook import (
    HOOK_TRICKS,
    OBSCURE_WRITING_GUIDE,
    SCIENTIFIC_WRITING_GUIDE,
    format_menu,
)
from reel_af.agents.distiller import ArticleSummary
from reel_af.agents.reel_composer import (
    CloseTrickId,
    Direction,
    HookTrickId,
    ReelDraft,
    RetentionTrickId,
    VoiceTone,
)

NUM_HOOKS = 8
NUM_FINALISTS = 2


# ───── Step 1 — hook candidates ─────────────────────────────────────


class _HookCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: str = Field(..., description="Literal first 4-7 spoken words. Punctuated.")
    # str (not Literal) because we accept either HOT trick ids OR OBSCURE
    # opener template ids depending on the article's topic_familiarity.
    hook_trick: str = Field(..., description="Which menu trick / template this hook executes.")
    direction: Direction = Field(..., description="Direction this hook implies for the body.")


class _HookBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hooks: list[_HookCandidate] = Field(..., min_length=NUM_HOOKS, max_length=NUM_HOOKS)


def _hook_gen_system(
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> str:
    """Build the hook-generation prompt — branches on content_mode AND
    topic_familiarity. Three regimes:
      • scientific  → SCIENTIFIC_WRITING_GUIDE (lead with result, jargon ok)
      • general+obscure → OBSCURE_WRITING_GUIDE (define before judge)
      • general+hot → cold-open HOOK_TRICKS menu (Hormozi-style)
    """
    if content_mode == "scientific":
        return f"""You are generating {NUM_HOOKS} OPENING HOOKS for a vertical
reel about a SCIENTIFIC PAPER. Audience is engineers / dev-Twitter /
technically-literate public — they know transformer, RL, gradient, MMLU
etc. Don't waste a beat defining things they know.

Read the guide below and apply it. These are PRINCIPLES, not templates.
Pick the shape that fits THIS paper.

{SCIENTIFIC_WRITING_GUIDE}

YOUR TASK: produce {NUM_HOOKS} distinct opening hooks (4-10 words each)
that follow the guide. Lead with the result, the number, or the
contribution. Vary the SHAPE across candidates — some can be
number-shock, some claim-with-baseline, some named-method drop, some
counter-intuitive-mechanism. For the `hook_trick` field, write a short
snake_case label describing your move."""

    if topic_familiarity == "obscure":
        return f"""You are generating {NUM_HOOKS} OPENING HOOKS for a vertical reel
about an OBSCURE topic — a niche product, specific researcher, inside-baseball
concept, or specialised research finding. The general scrolling viewer has
ZERO context. They've never heard of this thing.

Read the writing guide below and apply it. These are PRINCIPLES — do not
treat them as fill-in-the-blank templates. Pick the shape that best fits
THIS article.

{OBSCURE_WRITING_GUIDE}

YOUR TASK: produce {NUM_HOOKS} distinct opening hooks (5-12 words each)
that follow the guide above. Vary the REGISTER and the SHAPE across the
{NUM_HOOKS} candidates — don't write {NUM_HOOKS} hooks that all start the
same way. Some can be questions, some statements, some analogies, some
mini-explainers. Whatever fits.

For the `hook_trick` field, write a short snake_case label describing the
move you're making (e.g. 'question_define_twist', 'analogy_then_take',
'til_framing'). Make up names that describe what you ACTUALLY did. The
downstream body writer will read your label as a hint about the shape
of script to write."""

    # HOT topics: cold-open is fine
    return f"""You are generating {NUM_HOOKS} OPENING HOOKS for a vertical reel
about a HOT topic — audience already has context. Cold-open with the
strongest contrarian / surprising take. ONLY the hook — first 4-7 spoken
words. Not the body.

A hook's job: stop a thumb in 500ms.

Rules:
  • Each hook must execute a DIFFERENT trick from the menu.
  • Hooks must reference the article's actual content — names, numbers, or
    specific framings from the summary.
  • NO generic AI tells ("Did you know", "In this video").
  • 4-7 words. The first 3 words carry the load.
  • Declare which trick + which direction each hook implies for the body.

{format_menu(HOOK_TRICKS, "HOOK TRICKS MENU")}

You must produce exactly {NUM_HOOKS} hooks, all distinct in trick choice."""


async def _gen_hooks(app: Any, summary: ArticleSummary) -> list[_HookCandidate]:
    user = (
        f"ARTICLE\n"
        f"  domain           : {summary.domain}\n"
        f"  content_mode     : {summary.content_mode}\n"
        f"  audience_level   : {summary.audience_level}\n"
        f"  topic_familiarity: {summary.topic_familiarity}\n"
        f"  thesis           : {summary.one_line_thesis}\n"
        f"  takeaway         : {summary.intended_takeaway}\n"
        f"  examples         : {summary.concrete_examples}\n"
    )
    system = _hook_gen_system(
        topic_familiarity=summary.topic_familiarity,
        content_mode=summary.content_mode,
    )
    out = await app.ai(system=system, user=user, schema=_HookBatch)
    return out.hooks


# ───── Step 2 — rank hooks ──────────────────────────────────────────


class _HookRank(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hook_index: int = Field(..., description="0-based index into the candidate list.")
    score: int = Field(..., ge=1, le=10, description="Predicted scroll-stop probability.")
    why: str = Field(..., description="1 sentence: what makes this hook stop a thumb.")


class _HookRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rankings: list[_HookRank] = Field(..., min_length=NUM_HOOKS, max_length=NUM_HOOKS)


_HOOK_CRITIC_SYSTEM = """You are scoring vertical-reel HOOKS on scroll-stop
probability. You are NOT nice. Most AI-generated hooks deserve 4-6, not 8-10.
Calibrate hard.

INSTANT FAIL (cap at 3):
  • Any generic opener: "Did you know", "Let me tell you", "In this video",
    "Today we…", "So basically…", "Imagine if…"
  • > 7 words (viewers don't read essays)
  • First word is a conjunction ("And", "So", "Because")
  • Weak verbs: "learn", "see", "discover", "explore", "understand"
  • Hook explains the topic instead of provoking curiosity
  • Hook could be about ANY article (not specific to this one)

DECENT (5-7):
  • Has some specificity (a noun from the article, a number)
  • Pattern interrupt but somewhat predictable
  • Reads naturally as spoken English

STRONG (8-9):
  • Specific noun/number/name from THIS article in the first 4 words
  • Genuine surprise — contradicts conventional wisdom OR drops a stake
  • Triggers immediate "wait, what?" — opens a curiosity gap

ELITE (10):
  • All of the above AND addresses the viewer directly with personal stakes
    ("Your hand can't taste a thing.")
  • Could be the opening line of a Hormozi / MrBeast / Hank Green reel

Rank EVERY hook in the input list. Most should land 4-7. If your top hook
scores < 8, you are NOT being strict enough — re-score harder. Tied scores
OK but break ties in your ordering by descending specificity to the article."""


async def _rank_hooks(
    app: Any, hooks: list[_HookCandidate], summary: ArticleSummary
) -> list[_HookRank]:
    listing = "\n".join(
        f"  [{i}] trick={h.hook_trick:20s} direction={h.direction:15s} hook={h.hook!r}"
        for i, h in enumerate(hooks)
    )
    user = f"ARTICLE THESIS: {summary.one_line_thesis}\n\nHOOK CANDIDATES:\n{listing}"
    out = await app.ai(system=_HOOK_CRITIC_SYSTEM, user=user, schema=_HookRanking)
    # Sort by score descending; resolve ties by original order (stable sort).
    return sorted(out.rankings, key=lambda r: -r.score)


# ───── Step 3 — body from hook ──────────────────────────────────────


def _body_system(
    hook: _HookCandidate,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> str:
    # Scientific-mode body takes precedence over obscure-mode — the audience
    # for scientific papers is technical, not general-scrolling.
    if content_mode == "scientific":
        mode_block = (
            f"\n\n──── SCIENTIFIC-PAPER GUIDE (applies to entire script) ────\n"
            f"This is a scientific paper for technical audiences. The hook "
            f"above set up the result; the body should sustain the same "
            f"technical-but-clear register. Numbers, named methods, baselines, "
            f"comparisons — don't pad with conversational filler.\n\n"
            f"{SCIENTIFIC_WRITING_GUIDE}\n"
        )
    elif topic_familiarity == "obscure":
        mode_block = (
            f"\n\n──── OBSCURE-TOPIC GUIDE (applies to BODY, not just hook) ────\n"
            f"This topic is OBSCURE for the audience. The hook above established "
            f"the subject; your BODY must continue in the SAME conversational "
            f"explainer register and stay plain-language throughout. Don't "
            f"switch to punchy creator voice in sentence 2 — that breaks the "
            f"contract the hook made with the viewer.\n\n"
            f"{OBSCURE_WRITING_GUIDE}\n"
        )
    else:
        mode_block = ""

    return f"""You are completing a vertical-reel script that will be read by a
TTS engine and shown over generated video. The HOOK is locked:

  hook : {hook.hook!r}
  trick: {hook.hook_trick}
  direction: {hook.direction}

Your job: write the body + close so the WHOLE script reads as one performance.

NARRATION RHYTHM (this is what makes TTS not sound robotic):
  • Vary sentence length deliberately. Short. Then long-and-flowing. Short.
  • Use repetition for emphasis when the moment earns it: "They shut. It.
    Down." Three single-word periods reads as three deliberate beats.
  • Em-dashes (—) for ONE dramatic pause per script.
  • Commas where a human speaker would breathe.
  • ALL CAPS for ONE word maximum — only at the script's emotional peak.

CLOSE — the last 1-2 sentences must LAND. End on a strong noun or verb,
not a hedge. Pick the close shape that fits the script's energy: a
callback that re-frames the hook, a direct address that prompts a save,
a quotable line, a question that earns a comment. Never trail off.

CONTENT (faithful to source):
  • Use the article's actual examples, names, numbers from the summary.
  • Never invent metaphors not in the source.
  • Second person where natural.
  • No AI tells ("Did you know", "In this video", "Today we…").

OPENING:
  • Start with the locked hook verbatim. No rewrite.
  • Then deliver the payoff the hook promised.

TOTAL: 55-68 words (~21-26s spoken). Tight is better.
{mode_block}
Return a ReelDraft. Set:
  direction      = {hook.direction}
  hook_trick     = "{hook.hook_trick}"   (use this exact string; it may
                                          be a free-form label from an
                                          obscure-topic hook)
  retention_trick: your pick
  close_trick    : your pick
  voice_tone     : your pick
  script         : one continuous paragraph starting with the locked hook.
  viral_score    : honest 1-10."""


async def _body_from_hook(
    app: Any, hook: _HookCandidate, summary: ArticleSummary
) -> ReelDraft:
    user = (
        f"ARTICLE SUMMARY\n"
        f"  topic_familiarity: {summary.topic_familiarity}\n"
        f"  thesis           : {summary.one_line_thesis}\n"
        f"  takeaway         : {summary.intended_takeaway}\n"
        f"  examples         : {summary.concrete_examples}\n"
        f"  key points:\n"
        + "\n".join(f"    {i+1}. {p}" for i, p in enumerate(summary.key_points))
    )
    draft = await app.ai(
        system=_body_system(
            hook,
            topic_familiarity=summary.topic_familiarity,
            content_mode=summary.content_mode,
        ),
        user=user, schema=ReelDraft,
    )
    # For obscure topics the hook generator returns free-form labels (e.g.
    # "question_define_twist") which don't match the HOT-trick Literal in
    # ReelDraft.hook_trick. Trust whatever HOT-trick the body writer picked
    # for the schema; the audit trail comes from the body's choice, not the
    # original obscure label.
    return draft


# ───── Step 4 — final picker ────────────────────────────────────────


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    winner_index: int = Field(..., ge=0, le=NUM_FINALISTS - 1)
    composite_score: float = Field(..., ge=1, le=10)
    why: str = Field(..., description="1-2 sentences: why the winner beats the other.")


_FINAL_PICKER_SYSTEM = """You're picking between 2 complete reel scripts.
Pick the one with stronger overall scroll-stop + retention + close. Score
the winner's composite 1-10 (the WHOLE script, not just the hook).

Penalties: invented examples, generic openings, weak closes, wandering.
Rewards: faithful use of article examples, named tricks executed, strong
hook + strong close."""


async def _final_pick(
    app: Any, scripts: list[ReelDraft], summary: ArticleSummary
) -> tuple[int, float, str]:
    listing = "\n\n".join(
        f"=== SCRIPT [{i}] ===\n"
        f"direction={d.direction}\nhook_trick={d.hook_trick}\n"
        f"retention_trick={d.retention_trick}\nclose_trick={d.close_trick}\n"
        f"\n{d.script}"
        for i, d in enumerate(scripts)
    )
    user = f"ARTICLE THESIS: {summary.one_line_thesis}\n\n{listing}"
    v = await app.ai(system=_FINAL_PICKER_SYSTEM, user=user, schema=_Verdict)
    return v.winner_index, v.composite_score, v.why


# ───── Public entrypoint ────────────────────────────────────────────


async def run(app: Any, summary: ArticleSummary) -> ArchOutput:
    t0 = time.time()
    trace: list[str] = []

    # 1. Generate 8 hooks.
    hooks = await _gen_hooks(app, summary)
    trace.append(f"generated {len(hooks)} hooks")
    for h in hooks:
        trace.append(f"  - [{h.hook_trick:20s}] {h.hook!r}")

    # 2. Rank.
    ranked = await _rank_hooks(app, hooks, summary)
    trace.append("ranked hooks:")
    for r in ranked[:3]:
        trace.append(f"  {r.score}/10  {hooks[r.hook_index].hook!r}  — {r.why}")
    finalists = [hooks[r.hook_index] for r in ranked[:NUM_FINALISTS]]

    # 3. Bodies in parallel.
    bodies = await asyncio.gather(
        *(_body_from_hook(app, h, summary) for h in finalists),
        return_exceptions=True,
    )
    bodies = [b for b in bodies if isinstance(b, ReelDraft)]
    if not bodies:
        raise RuntimeError("arch_b: all body generations failed.")
    trace.append(f"completed {len(bodies)} full scripts from top hooks")

    # 4. Final pick.
    if len(bodies) == 1:
        winner_idx, score, why = 0, bodies[0].viral_score, "only one candidate"
    else:
        winner_idx, score, why = await _final_pick(app, bodies, summary)
    trace.append(f"final pick: script[{winner_idx}]  composite={score:.1f}  ({why})")

    return ArchOutput(
        arch_id="B",
        arch_name="Hook-First Cascade",
        bet="Hook is 80% of the success — optimize it in isolation first.",
        draft=bodies[winner_idx],
        self_score=score,
        wall_time_s=time.time() - t0,
        trace=trace,
    )
