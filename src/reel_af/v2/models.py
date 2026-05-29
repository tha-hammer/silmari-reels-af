"""Pydantic models for the v2 pipeline.

All closed-shape (extra='forbid') so OpenAI / Bedrock strict structured-output
modes accept them. Schemas flow:

    URL → Essence → ScriptDraft → (audio + word_timings) → list[Shot]
    Shot → ShotVisual, Shot → AccentOverlay | None
    Shot + ShotVisual → first frame → Veo clip → stitched mp4

Naming follows the archei rule from /Users/santoshkumarradha/Documents/agentfield/
code/CLAUDE.md: structured JSON when downstream code routes on it; strings
when downstream LLMs reason over it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ───── Phase 1 — extract ────────────────────────────────────────────


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


# ───── Phase 2 — compose ────────────────────────────────────────────


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
        le=170,
        description=(
            "150 for general content, 130 for scientific. Determines TTS "
            "pacing and overall reel length."
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
        """Loose check: any noun-ish keyword from the hook appears in the last
        clause of the narration. We don't have a full POS tagger here so we
        approximate: take the longest word from the hook (≥4 chars, not a
        stopword) and require it to appear in the final 12 words.

        Strict callback enforcement is upgraded in a follow-up; for v1 this
        catches the obvious miss-cases (model writes a hook then closes on
        an unrelated thought).
        """
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
        # Longest first — most distinctive.
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


# ───── Phase 3 — TTS ────────────────────────────────────────────────


class WordTiming(BaseModel):
    """One word from the TTS output, with start/end times in seconds."""

    model_config = ConfigDict(extra="forbid")

    word: str = Field(..., description="The literal word as produced by TTS.")
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)


# ───── Phase 4 — plan_shots (deterministic, no LLM) ─────────────────


class Card(BaseModel):
    """One subtitle card — 2-5 words burned on screen at once.

    Snap to word boundaries; bounded by char width (Montserrat Bold metrics).
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    words: list[WordTiming]
    start_s: float
    end_s: float
    line_count: int = Field(..., ge=1, le=2)


ShotRole = Literal["hook", "mechanism", "payoff"]


class Shot(BaseModel):
    """One visual unit — one Veo i2v call. Contains 1-4 subtitle cards.

    Duration is guaranteed ≤ 7.0s by the planner so the chosen Veo bucket
    (one of {4,6,8}) always has headroom over the audio.
    """

    model_config = ConfigDict(extra="forbid")

    idx: int = Field(..., ge=0)
    cards: list[Card] = Field(..., min_length=1)
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)
    duration_s: float = Field(
        ...,
        gt=0,
        le=8.0,
        description=(
            "Prefer ≤ 7.0s (8s Veo bucket with 1s safety). Up to 8.0s "
            "tolerated when a single card has unusually long internal "
            "pauses from the TTS — uses the 8s bucket with no safety."
        ),
    )
    role: ShotRole
    veo_duration: Literal[4, 6, 8] = Field(
        ...,
        description=(
            "Smallest Veo bucket ≥ duration_s + 1.0s safety. The video "
            "clip is generated at this length then trimmed during stitch."
        ),
    )


# ───── Phase 5 — visual + accent (parallel .ai per shot) ────────────


MotionHint = Literal[
    "static", "slow_zoom_in", "slow_zoom_out",
    "pan_left", "pan_right", "ken_burns",
]


class ShotVisual(BaseModel):
    """Visual plan for one shot."""

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
            "Which specific evidence piece this shot grounds on (one of "
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
    """Optional editorial accent burned on screen alongside Layer 1 subtitles.

    Not every shot has one. The accent agent emits None for shots where the
    verbatim subtitles alone carry the load.
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


# ───── Render artifacts ─────────────────────────────────────────────


class ShotArtifact(BaseModel):
    """Per-shot generated media — first frame + Veo clip."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    idx: int
    first_frame_path: Optional[Path] = None
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None


class ReelV2Result(BaseModel):
    """Final result returned by the v2 pipeline."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_path: Path
    duration_s: float
    narration: str
    hook: str
    hook_variant: HookVariant
    content_mode: ContentMode
    target_wpm: int
    domain: str
    shot_count: int
    card_count: int
    accent_count: int = Field(
        ...,
        description="How many shots emitted an accent overlay (Layer 2).",
    )
    run_id: str
    timings: dict[str, float] = Field(default_factory=dict)
    wall_time_s: float = 0.0
