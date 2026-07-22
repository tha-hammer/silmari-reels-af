"""Pydantic schemas for the reel-af pipelines.

Two pipelines share these models:

  Article → Reel  (URL → vertical viral reel)
    Essence → ScriptDraft → WordTiming[]/Card[]/Beat[]
                          → BeatVisual + AccentOverlay
                          → BeatArtifact → final mp4

  Topic   → Reel  (topic string → vertical viral reel)
    EssenceCandidate[] (4 parallel hunters)
      → CriticOutput.chosen_indices
        → ConversationalScript[]   (parallel narrators)
          → PairwiseVerdict        (judge picks winner)
            → maps onto the same downstream Beat/Card/BeatVisual flow

All schemas use ``extra="forbid"`` so OpenAI / Bedrock strict structured-
output modes accept them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

# ════════════════════════════════════════════════════════════════════
# Article → Reel — Phase 1: extract
# ════════════════════════════════════════════════════════════════════


ContentMode = Literal["general", "scientific"]


class Essence(BaseModel):
    """The single thing the reel is about. One harness call extracts this."""

    model_config = ConfigDict(extra="forbid")

    core_claim: str = Field(
        ...,
        description=(
            "The single most surprising or counter-intuitive thing in this "
            "article, in one sentence the author would recognize. ≤25 words. "
            "This is the hook's raw material."
        ),
    )
    mechanism: str = Field(
        ...,
        description=(
            "Why the claim is true / how it works. 1-2 sentences. The "
            "explanation that pays off the hook in the body of the reel."
        ),
    )
    evidence: list[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description=(
            "1-3 concrete pieces of evidence from the article: numbers, named "
            "entities, specific examples. Verbatim or near-verbatim. These "
            "ground every downstream visual and accent overlay."
        ),
    )
    content_mode: ContentMode = Field(
        ...,
        description=(
            "'scientific' = research paper / preprint / technical writeup; "
            "audience is engineers and the technically-literate public. "
            "'general' = everything else."
        ),
    )
    domain: str = Field(
        ...,
        description="One word for the subject domain — used for visual style.",
    )


# ════════════════════════════════════════════════════════════════════
# Article → Reel — Phase 2: compose script
# ════════════════════════════════════════════════════════════════════


HookVariant = Literal[
    "shock_stat",      # open with a specific number
    "contrarian",      # "Everyone thinks X. They're wrong."
    "authority",       # "A physicist explains why..."
    "curiosity_gap",   # state outcome, withhold mechanism
    "listicle",        # "3 things you didn't know about..."
]


class ScriptDraft(BaseModel):
    """The full narration for the reel, written in one .ai() call."""

    model_config = ConfigDict(extra="forbid")

    hook: str = Field(
        ...,
        description=(
            "The literal first 6-10 spoken words. Punctuated. This is what "
            "scrolls past in the first 1-2 seconds."
        ),
    )
    hook_variant: HookVariant = Field(
        ...,
        description="Which canonical hook shape this is. For audit trail.",
    )
    mechanism_lines: list[str] = Field(
        ...,
        min_length=2,
        max_length=4,
        description=(
            "2-4 sentences explaining the why behind the hook. Each sentence "
            "is a coherent visual beat downstream."
        ),
    )
    payoff_line: str = Field(
        ...,
        description=(
            "The closing 1 sentence that callbacks the hook. Must end on a "
            "strong noun or verb — no hedges, no 'thanks for watching.' "
            "The last few words must echo a keyword from the hook so the "
            "viewer can loop back to the opening."
        ),
    )
    target_wpm: int = Field(
        ...,
        ge=120,
        le=200,
        description=(
            "180 for general (fast viral pace), 175 for scientific. Pauses "
            "kill retention, so we keep this high."
        ),
    )
    enforce_loop_back: SkipJsonSchema[bool] = Field(
        default=True,
        description=(
            "AF-vjm: opt-out for the loop-back gate, used only by the topic "
            "path's last-resort safety net when no narration candidate "
            "passes. Hidden from the LLM schema (SkipJsonSchema) so the "
            "article compose model can never set it; article drafts always "
            "default to True (strict). Declared before `narration` so the "
            "validator can read it from info.data."
        ),
    )
    narration: str = Field(
        ...,
        description=(
            "The full concatenated script (hook + mechanism + payoff) as ONE "
            "string with inline Gemini TTS audio tags [excited], [pause], "
            "[whispers] etc. inserted at the right beats. This is what gets "
            "passed to TTS verbatim."
        ),
    )

    @field_validator("narration")
    @classmethod
    def _loop_back_check(cls, v: str, info) -> str:
        """Loose check: any noun-ish keyword from the hook appears in the
        last clause of the narration. We approximate by requiring the
        longest non-stopword from the hook (≥4 chars) to appear in the
        final 12 words. Catches the obvious miss-cases where the model
        writes a hook then closes on an unrelated thought.
        """
        if info.data.get("enforce_loop_back", True) is False:
            # AF-vjm: topic-path safety net — gate explicitly relaxed after
            # every narration candidate missed the loop-back.
            return v
        hook = info.data.get("hook") or ""
        if not hook or not v:
            return v
        stopwords = {
            "the", "and", "but", "for", "with", "this", "that", "you",
            "your", "are", "was", "were", "they", "them", "from", "have",
            "has", "had", "what", "when", "why", "how", "will", "would",
            "could", "should", "into", "their", "there", "than", "then",
        }
        hook_words = [
            w.strip(".,!?—-:;'\"").lower()
            for w in hook.split()
            if len(w.strip(".,!?—-:;'\"")) >= 4
            and w.strip(".,!?—-:;'\"").lower() not in stopwords
        ]
        if not hook_words:
            return v
        hook_words.sort(key=len, reverse=True)
        last_clause = " ".join(v.lower().split()[-12:])
        for hw in hook_words:
            if hw in last_clause:
                return v
        raise ValueError(
            f"Loop-back missing: no hook keyword from {hook_words[:3]} appears "
            f"in the last 12 words of the narration. Rewrite the close so the "
            f"final clause callbacks the opening line."
        )


# ════════════════════════════════════════════════════════════════════
# Phase 3: TTS — per-word timings
# ════════════════════════════════════════════════════════════════════


class WordTiming(BaseModel):
    """One word from the TTS output, with start/end times in seconds."""

    model_config = ConfigDict(extra="forbid")

    word: str = Field(..., description="The literal word as produced by TTS.")
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)


# ════════════════════════════════════════════════════════════════════
# Phase 4: subtitle layout (deterministic, no LLM)
# ════════════════════════════════════════════════════════════════════


class Card(BaseModel):
    """One subtitle card — 2-5 words that share a libass layout window.

    Snap to word boundaries; bounded by char width (Montserrat Bold metrics).
    Cards drive ONLY the karaoke layout — not video boundaries.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    words: list[WordTiming]
    start_s: float
    end_s: float
    line_count: int = Field(..., ge=1, le=2)


# ════════════════════════════════════════════════════════════════════
# Phase 5: video planning — beats are the visual unit
# ════════════════════════════════════════════════════════════════════


BeatRole = Literal["hook", "mechanism", "payoff"]


class Beat(BaseModel):
    """One narrative beat — the visual planning unit.

    A reel has ~5 beats: 1 hook, 2-3 mechanism lines, 1 payoff. Each beat
    gets ONE Veo i2v clip whose duration is a fixed bucket (4 / 6 / 8 s).
    The audio timeline is the master clock; beats are placed end-to-end on it.
    """

    model_config = ConfigDict(extra="forbid")

    idx: int = Field(..., ge=0)
    role: BeatRole
    text: str = Field(
        ...,
        description=(
            "The narrative text for this beat (one sentence from the "
            "script). Drives the visual prompt; never burned on screen "
            "verbatim — karaoke subtitles come from per-word cards instead."
        ),
    )
    target_duration_s: float = Field(
        ...,
        gt=0,
        description=(
            "Estimated audio duration for this beat in seconds. Used only "
            "to pick the Veo bucket; the final timing is audio-master."
        ),
    )
    veo_duration: Literal[4, 6, 8] = Field(
        ...,
        description=(
            "Fixed Veo bucket for this beat's video clip. Picked from the "
            "smallest bucket ≥ target_duration_s with a small safety margin."
        ),
    )


MotionHint = Literal[
    "static", "slow_zoom_in", "slow_zoom_out",
    "pan_left", "pan_right", "ken_burns",
]


class BeatVisual(BaseModel):
    """Visual plan for one beat."""

    model_config = ConfigDict(extra="forbid")

    image_prompt: str = Field(
        ...,
        description=(
            "Prompt for the first-frame image generator. Specific, visual, "
            "9:16-friendly composition. Grounded in the article's actual "
            "evidence — names, numbers, specific things. NOT generic mood."
        ),
    )
    motion_hint: MotionHint
    visual_anchor: str = Field(
        ...,
        description=(
            "Which specific evidence piece this beat grounds on (one of "
            "essence.evidence). For audit/debugging."
        ),
    )


AccentPattern = Literal[
    "number",            # e.g. "$47,000" or "85%"
    "named_entity",      # e.g. "DR. CHEN, STANFORD"
    "jargon_translation",  # e.g. "entanglement = spooky link"
    "hook_title_card",   # first-frame bold question or claim
    "reaction",          # e.g. "WAIT WHAT", "NO WAY"
    "list_marker",       # e.g. "STEP 2 OF 3"
]

AccentPosition = Literal["lower_third", "upper_third"]


class AccentOverlay(BaseModel):
    """Optional editorial accent burned alongside Layer 1 subtitles.

    Not every beat has one. The accent agent emits None for beats where
    the verbatim subtitles alone carry the load.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="2-6 words. UPPERCASE rendered by stitch.",
    )
    pattern: AccentPattern
    position: AccentPosition = Field(
        default="lower_third",
        description=(
            "Opposite third of the frame from Layer 1 subtitles. Default "
            "is lower_third because subtitles default to upper-center."
        ),
    )


class BeatArtifact(BaseModel):
    """Per-beat generated media — first frame + Veo clip."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    idx: int
    first_frame_path: Optional[Path] = None
    video_path: Optional[Path] = None


# ════════════════════════════════════════════════════════════════════
# Topic → Reel — Phase 1: hunters + critic
# ════════════════════════════════════════════════════════════════════


HunterAngle = Literal[
    "specific_figure", "reversal", "temporal", "cross_domain"
]


class EssenceCandidate(BaseModel):
    """One candidate essence from a hunter — a draft Essence plus
    metadata about which angle generated it and why it's novel."""

    model_config = ConfigDict(extra="forbid")

    core_claim: str = Field(
        ...,
        description=(
            "The surprising claim, ≤25 words. Must be specific (named "
            "person / specific number / specific year), not vague."
        ),
    )
    mechanism: str = Field(
        ...,
        description=(
            "Why or how the claim is true. 1-2 sentences. The body of "
            "the future narration."
        ),
    )
    evidence: list[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description=(
            "1-3 verifiable specifics: a named person + year, a study "
            "with sample size, a documented event. NOT generic 'studies "
            "show'."
        ),
    )
    domain: str = Field(
        ...,
        description="One-word domain — e.g. 'philosophy', 'biology'.",
    )
    angle: HunterAngle = Field(
        ...,
        description="Which hunter generated this candidate.",
    )
    novelty_pitch: str = Field(
        ...,
        description=(
            "One sentence explaining why MOST people haven't heard this. "
            "If you can't argue novelty, the claim isn't viral enough."
        ),
    )


class HuntBatch(BaseModel):
    """One hunter's output: 3 EssenceCandidates."""

    model_config = ConfigDict(extra="forbid")
    candidates: list[EssenceCandidate] = Field(
        ..., min_length=3, max_length=3,
    )


class RankedCandidate(BaseModel):
    """Critic's score for one candidate."""

    model_config = ConfigDict(extra="forbid")
    candidate_idx: int = Field(..., ge=0)
    novelty: int = Field(..., ge=1, le=10)
    specificity: int = Field(..., ge=1, le=10)
    hookability: int = Field(..., ge=1, le=10)
    narratability: int = Field(..., ge=1, le=10)
    composite: float = Field(..., ge=1, le=10)
    why: str = Field(..., description="1 sentence: what beat what.")


class CriticOutput(BaseModel):
    """Critic returns rankings for ALL candidates + picked top N indices."""

    model_config = ConfigDict(extra="forbid")
    rankings: list[RankedCandidate] = Field(..., min_length=1)
    chosen_indices: list[int] = Field(
        ...,
        description="The top N candidate indices to write narrations for.",
        min_length=1,
        max_length=5,
    )


# ════════════════════════════════════════════════════════════════════
# Topic → Reel — Phase 2: delayed-reveal narration
# ════════════════════════════════════════════════════════════════════


OpenStyle = Literal[
    "question",         # "Why is X so weird?" / "What makes X impossible?"
    "setup_flip",       # "You think X is about Y. It's not."
    "cryptic_setup",    # "X has a secret. It's not what you think."
    "topic_tease",      # "Let's talk about X. Biology textbooks have it wrong."
    "personal_stake",   # "Your body has a secret. It's not even you."
]


class ConversationalScript(BaseModel):
    """A 25-30s vertical reel script in DELAYED-REVEAL style.

    Unlike hook-first scripts (which front-load the surprise), this style
    leads with curiosity. The TEASE poses the question; the body delivers
    the answer 8-15 seconds in. That delay is the engagement engine — the
    viewer keeps watching to find out.
    """

    model_config = ConfigDict(extra="forbid")

    tease: str = Field(
        ...,
        min_length=10,
        max_length=140,
        description=(
            "The opening hook — a question, setup, or tease. MUST NOT "
            "contain the answer. No named person, no specific year here. "
            "5-15 spoken words. The viewer asks themselves the question."
        ),
    )
    common_belief: str | None = Field(
        None,
        max_length=200,
        description=(
            "Optional: one sentence stating what most people assume. "
            "Sets up the flip in the reveal. Omit if the tease is "
            "strong on its own."
        ),
    )
    reveal: str = Field(
        ...,
        min_length=60,
        description=(
            "The body — 2-3 sentences delivering the surprising answer. "
            "THIS is where named people, specific years, and mechanism "
            "live. Build the argument."
        ),
    )
    payoff: str = Field(
        ...,
        min_length=10,
        max_length=200,
        description=(
            "The close — 1 sentence. SHOULD callback the tease — repeat "
            "a distinctive word from the tease or rephrase the opening "
            "question with the answer now known."
        ),
    )
    open_style: OpenStyle = Field(
        ...,
        description="Which canonical opening shape this tease uses.",
    )
    target_wpm: int = Field(
        default=180,
        ge=160,
        le=200,
        description="Fast delivery default. 180 WPM ≈ tight creator pace.",
    )
    narration: str = Field(
        ...,
        min_length=150,
        description=(
            "Full concatenated narration (tease + optional common_belief "
            "+ reveal + payoff) with inline Gemini TTS tags interleaved "
            "for delivery direction. This is what gets passed verbatim "
            "to TTS."
        ),
    )


class PairwiseVerdict(BaseModel):
    """Judge's pick among N candidate narrations."""

    model_config = ConfigDict(extra="forbid")
    winner_idx: int = Field(..., ge=0)
    composite_score: float = Field(..., ge=1, le=10)
    why: str = Field(..., description="1-2 sentences.")


__all__ = [
    "AccentOverlay",
    "AccentPattern",
    "AccentPosition",
    "Beat",
    "BeatArtifact",
    "BeatRole",
    "BeatVisual",
    "Card",
    "ContentMode",
    "ConversationalScript",
    "CriticOutput",
    "Essence",
    "EssenceCandidate",
    "HookVariant",
    "HuntBatch",
    "HunterAngle",
    "MotionHint",
    "OpenStyle",
    "PairwiseVerdict",
    "RankedCandidate",
    "ScriptDraft",
    "WordTiming",
]
