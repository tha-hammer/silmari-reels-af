"""Phase 2 compose: Essence -> ScriptDraft in ONE .ai() call.

Replaces the v1 LLM tower: route_and_run + arch_b (8-hook ideate + critic-rank
+ 2 body-drafts + pick) + arch_f (exemplar clone) + arch_i (hybrid) +
tag_injector. Five chained LLM stages collapse into one fixed-shape call.

The schema's loop-back validator (ScriptDraft._loop_back_check) is the safety
net; the prompt below teaches the model to satisfy it the first time so we
don't burn a retry on the obvious miss-case.
"""

from __future__ import annotations

from typing import Any

from reel_af.agents.creator_playbook import SCIENTIFIC_WRITING_GUIDE
from reel_af.v2.models import Essence, ScriptDraft

# ────────────────────────────────────────────────────────────────────
# Tag vocabulary — kept in sync with reel_af.agents.tag_injector.
# These are hints Gemini 3.1 Flash TTS treats as stage directions and never
# speaks aloud. The model is free to use any Gemini-supported tag that fits.
# ────────────────────────────────────────────────────────────────────
_TAG_VOCAB = """\
  Emotion           : [excited] [curious] [serious] [warm] [thoughtful]
                      [confident] [playful] [intense] [hopeful]
  Pacing            : [pause] [pause short] [pause long] [breath]
                      [slow] [fast] [building]
  Volume / weight   : [whispers] [quiet] [emphasis] [loud]
  Sentiment         : [wonder] [surprise] [skeptical]
"""


def _system_prompt(content_mode: str) -> str:
    if content_mode == "scientific":
        word_target = "50-58 words"
        wpm = 130
        register = (
            "TECHNICAL register. Audience is engineers and the technically-"
            "literate public. Use field jargon freely. Define only paper-"
            "specific terms inline in 5-8 words on first use."
        )
        hook_menu = (
            "For scientific content prefer `authority` or `shock_stat`. "
            "`curiosity_gap` works if the result is genuinely surprising."
        )
        mode_block = (
            "\n──── SCIENTIFIC WRITING GUIDE (applies to the WHOLE script) ────\n"
            f"{SCIENTIFIC_WRITING_GUIDE}\n"
        )
    else:  # general
        word_target = "62-70 words"
        wpm = 150
        register = (
            "CONVERSATIONAL register. Audience is general scrolling viewers. "
            "Plain language; second person where natural. No jargon without "
            "a one-clause translation."
        )
        hook_menu = (
            "For general content prefer `shock_stat`, `contrarian`, or "
            "`curiosity_gap`. `listicle` only if the article is structurally "
            "a list. `authority` only if the source's expert is the story."
        )
        mode_block = ""

    return f"""You are writing a 25-second vertical reel narration.

The structure is FIXED. Do not deviate.

  1. HOOK            — 6-10 spoken words. Picks ONE variant from:
                       shock_stat | contrarian | authority | curiosity_gap | listicle
                       Declare which variant you chose in `hook_variant`.
                       {hook_menu}

  2. MECHANISM       — 2-4 sentences that explain the WHY behind the hook.
                       Each sentence is a coherent visual beat downstream
                       (one shot per sentence), so each must stand alone.
                       Names, numbers, specific things — not vibes.

  3. PAYOFF + LOOP   — 1 closing sentence. The last 4-8 words MUST echo a
                       distinctive word from your HOOK: a noun, a number,
                       or a named entity — NOT a stopword (not "the", "and",
                       "that", "this", "you"). This is how the viewer loops
                       back to the start. The schema validator checks this
                       literally; if you skip it the call fails.

REGISTER: {register}

TOTAL LENGTH: {word_target}. Set `target_wpm` to {wpm}. The reel lands at ~25s.

──── INLINE TTS TAGS ────
The `narration` field is passed VERBATIM to Gemini 3.1 Flash TTS. Insert
inline stage-direction tags in [square brackets] at the right beats. Tags
go BEFORE the clause they modify. Gemini never speaks them aloud.

Tag vocabulary (hints, not closed — Gemini supports 200+):
{_TAG_VOCAB}
Place tags where a human performer would shift gear: the cold open of the
hook, before the punchline reveal, before the payoff, around any em-dash.
Don't tag every clause — taste matters. A typical 25s reel has 5-9 tags.

──── ANTI-PATTERNS — instant rejection ────
  • "Hey guys", "Did you know", "In this video", "Today we…"
  • "Thanks for watching", "Don't forget to like", "Smash that subscribe"
  • Generic CTAs ("Follow for more", "Comment below").
  • Fade-out closes that trail into nothing.
  • Hedges in the close ("kind of", "sort of", "might be", "maybe").
  • Padding the word count with filler — tight is better than long.
  • Inventing facts, names, or numbers not in the essence below.

──── OUTPUT FIELDS ────
  hook              : the literal first 6-10 spoken words, punctuated.
  hook_variant      : which canonical shape — for the audit trail.
  mechanism_lines   : list of 2-4 sentences (no leading bullets).
  payoff_line       : the closing sentence, with the loop-back keyword.
  target_wpm        : {wpm}.
  narration         : hook + mechanism + payoff concatenated as ONE string,
                      with inline [tags] inserted. Same words, same order,
                      same punctuation as the structured fields — only tags
                      added. This string is what TTS speaks.
{mode_block}"""


def _user_prompt(essence: Essence) -> str:
    """Mirror arch_b_hook_first._body_from_hook's user payload shape, sourced
    from Essence instead of ArticleSummary."""
    evidence_block = "\n".join(
        f"    {i + 1}. {e}" for i, e in enumerate(essence.evidence)
    )
    return (
        f"ESSENCE (from the source article — use these facts, invent nothing)\n"
        f"  content_mode : {essence.content_mode}\n"
        f"  domain       : {essence.domain}\n"
        f"  core_claim   : {essence.core_claim}\n"
        f"  mechanism    : {essence.mechanism}\n"
        f"  evidence:\n"
        f"{evidence_block}\n\n"
        f"Write the ScriptDraft now. The hook draws from `core_claim`; the "
        f"mechanism_lines unpack `mechanism` using the evidence above; the "
        f"payoff_line lands on a word that callbacks the hook."
    )


async def compose_script(app: Any, essence: Essence) -> ScriptDraft:
    """One .ai() call. Fixed Hook -> Mechanism -> Payoff -> Loop structure.
    Parameterized by content_mode. Inline TTS tags in the narration."""
    return await app.ai(
        system=_system_prompt(essence.content_mode),
        user=_user_prompt(essence),
        schema=ScriptDraft,
    )
