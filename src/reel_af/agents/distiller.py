"""Distiller — pure comprehension. What does this article actually say?

This step does NO creative work. It reads the article and pulls out the
author's real thesis, the real points they make, the takeaway they intend.
The composer downstream then decides how to present it.

Why this is a separate step: when comprehension and presentation share one
prompt, the model conflates them — it starts inventing examples instead of
using the article's own, and the output drifts from the source. Separating
"what does this say" from "how do I present it" keeps the condensation
faithful.

Context strategy:
  IN  : full article body + claims (everything we know about the source)
  OUT : structured summary — thesis, key points, examples, takeaway
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reel_af.models import SourceContent

# Number of key points to extract. 3-5 keeps the reel tight; beyond 5 the
# downstream condenser would have to drop material anyway.
MIN_POINTS = 3
MAX_POINTS = 5


class JargonEntry(BaseModel):
    """One paired term + its plain-English translation.

    Closed-shape so OpenAI's strict structured-output mode accepts it
    (loose `dict[str, str]` fails on Bedrock + OpenAI strict).
    """

    model_config = ConfigDict(extra="forbid")
    term: str = Field(..., description="The specialist term as it appears.")
    plain: str = Field(..., description="4-word plain-English equivalent.")


class ArticleSummary(BaseModel):
    """Faithful structured summary of one article. No creative reframing."""

    model_config = ConfigDict(extra="forbid")

    one_line_thesis: str = Field(
        ...,
        description=(
            "What is this article arguing or telling us, in one sentence? Use the "
            "author's framing, not yours. If they make a claim, state the claim; if "
            "they tell a story, state what the story shows. Max 25 words."
        ),
    )
    key_points: list[str] = Field(
        ...,
        min_length=MIN_POINTS,
        max_length=MAX_POINTS,
        description=(
            f"{MIN_POINTS}-{MAX_POINTS} of the article's actual supporting points, "
            "in the order they appear or build. Each ≤ 25 words. These are the "
            "things the author wants you to know — not your reinterpretation."
        ),
    )
    concrete_examples: list[str] = Field(
        ...,
        min_length=0,
        max_length=5,
        description=(
            "Specific examples, names, numbers, or anecdotes from the article (≤5). "
            "These are the raw material the condenser will use to make the reel "
            "concrete instead of abstract. Verbatim or near-verbatim. Empty list if "
            "the article has no concrete examples."
        ),
    )
    intended_takeaway: str = Field(
        ...,
        description=(
            "What does the author want you to do, think, or feel after reading? "
            "One sentence. If the article doesn't have an explicit takeaway, infer "
            "the most plausible one."
        ),
    )
    domain: str = Field(
        ...,
        description=(
            "One word for the subject domain — e.g. 'technology', 'science', "
            "'business', 'philosophy', 'design', 'health'. Used downstream to pick "
            "an appropriate reel direction."
        ),
    )
    content_mode: Literal["general", "scientific"] = Field(
        ...,
        description=(
            "What KIND of content is this? "
            "'scientific' = a research paper / preprint / technical write-up "
            "with a method, result, baseline comparison; the reel should be "
            "for engineers and the technically-literate public, lead with the "
            "headline result, can use field jargon freely. "
            "'general' = any other article (news, blog, essay, opinion); the "
            "reel should be for a TikTok-scroller audience with no specialist "
            "knowledge. "
            "The caller can also override this via the URL pattern (arxiv, "
            "openreview, biorxiv) but you should ALSO classify based on the "
            "content itself — a Medium post explaining a paper is still "
            "'scientific' content."
        ),
    )
    audience_level: Literal["general", "technical", "expert"] = Field(
        ...,
        description=(
            "Best audience target for this material. "
            "'general' = no specialist knowledge (TikTok scroller). "
            "'technical' = engineers / dev-Twitter / scientifically-literate. "
            "'expert' = working in this exact subfield. We never target 'expert' "
            "(too narrow for a reel) — if you'd otherwise pick expert, downgrade "
            "to technical and prepare the writer to translate."
        ),
    )
    topic_familiarity: Literal["hot", "obscure"] = Field(
        ...,
        description=(
            "Honestly classify how familiar a general TikTok/Reels scroller is "
            "with this topic. "
            "'hot' = mainstream news, well-known brands/people, current AI hype, "
            "common consumer products — the audience already has a referent and "
            "you can cold-open with a contrarian hook. "
            "'obscure' = niche tech ('Omarchy', 'QKD'), specific academics "
            "('Ramanathan-Horodecki'), inside-baseball ('your dotfiles aren't a "
            "distro'), specialised research — the audience needs the subject "
            "INTRODUCED before any hook will land. "
            "When in doubt, pick 'obscure'. A confused viewer is a lost viewer."
        ),
    )
    plain_thesis: str = Field(
        ...,
        description=(
            "The thesis re-written for a TikTok scroller with high-school "
            "education, no specialty knowledge. One sentence. Replace EVERY "
            "specialist term with everyday language. No jargon at all. Read "
            "it aloud — would your aunt understand it on first hearing?"
        ),
    )
    jargon_glossary: list[JargonEntry] = Field(
        ...,
        description=(
            "Every specialist term in the article paired with a 4-word "
            "plain-English translation that a TikTok scroller would grasp. "
            "The downstream writer uses this to choose between (a) using the "
            "plain translation outright, or (b) defining the jargon inline. "
            "Aim for 4-10 entries. Empty list ONLY if the article genuinely "
            "has no jargon."
        ),
    )


_SYSTEM = """You're reading an article and producing a faithful structured
summary. This is pure comprehension — no creativity, no reframing, no clever
takes. You're not deciding how to present the article; another agent does
that. Your job is to make sure the next agent has the article's REAL content.

Rules:
  • Use the author's framing. If they call it X, you call it X.
  • Use the article's own examples, names, numbers. Don't invent new ones.
  • Don't add your own analysis or implications. Just report what's there.
  • Be specific. "He argues for X because of Y, Z" beats "He discusses X".
  • If the article is short or thin on examples, that's fine — empty lists
    are legitimate.

You ALSO classify two routing axes the downstream pipeline branches on:

  • content_mode = "scientific" if this is a research paper / preprint /
    technical writeup with a method-result-baseline shape, otherwise
    "general". A Medium post explaining a paper is still "scientific".

  • audience_level = "general" / "technical" / "expert" — best fit for
    this material. Be honest. If audience would have to be in the specific
    subfield, downgrade to "technical" and let the writer translate.

Output the thesis, plain thesis, jargon glossary, key points, examples,
intended takeaway, domain, content_mode, audience_level, and topic_familiarity."""


async def distill(app: Any, source: SourceContent) -> ArticleSummary:
    """Read the article, produce a faithful structured summary."""
    user = (
        f"ARTICLE\n"
        f"  title    : {source.title}\n"
        f"  audience : {source.audience_hints}\n\n"
        f"KEY CLAIMS (already extracted by the navigator — these are verbatim "
        f"quotable bits, raw material):\n"
        + "\n".join(f"  - {c}" for c in source.key_claims)
        + f"\n\nFULL BODY (truncated to fit context):\n{source.body[:14000]}"
    )
    return await app.ai(system=_SYSTEM, user=user, schema=ArticleSummary)
