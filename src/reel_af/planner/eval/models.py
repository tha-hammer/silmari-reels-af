"""Typed result models for the reel-quality eval harness."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DimensionName = Literal[
    "hook_strength_r1",
    "pacing_escalation_r3",
    "no_dead_air_r4",
    "template_payoff_match_r6",
    "loop_tie_back_r8",
    "specific_share_cue_r9",
    "no_engagement_bait_r11",
    "single_cta_r12",
]

RETENTION_DIMENSIONS: tuple[DimensionName, ...] = (
    "hook_strength_r1",
    "pacing_escalation_r3",
    "no_dead_air_r4",
    "template_payoff_match_r6",
    "loop_tie_back_r8",
    "specific_share_cue_r9",
    "no_engagement_bait_r11",
    "single_cta_r12",
)

DIMENSION_LABELS: dict[DimensionName, str] = {
    "hook_strength_r1": "Hook strength: R1 resolves within 3.5s",
    "pacing_escalation_r3": "Pacing escalation: R3 tightens toward payoff",
    "no_dead_air_r4": "No dead air: R4 avoids slow gaps",
    "template_payoff_match_r6": "One template and hook promise matches payoff: R6",
    "loop_tie_back_r8": "Loop tie-back: R8 final echoes hook",
    "specific_share_cue_r9": "Exactly one specific share cue: R9",
    "no_engagement_bait_r11": "No engagement bait: R11",
    "single_cta_r12": "At most one CTA: R12",
}


class DimensionScore(BaseModel):
    """One named retention-dimension score from the judge or hard gate."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=5)
    rationale: str = Field(min_length=1)


class GateCheck(BaseModel):
    """A deterministic pre-judge gate."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["verbatim_align", "retention_lint", "compile"]
    passed: bool
    summary: str
    score: float | None = None
    status: str | None = None
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class PreGateResult(BaseModel):
    """All deterministic gates that must pass before LLM judging."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[GateCheck]


class BeatEvidence(BaseModel):
    """Compact beat evidence supplied to the LLM judge."""

    model_config = ConfigDict(extra="forbid")

    index: int
    role: str | None = None
    span_quote: str
    start_s: float | None = None
    end_s: float | None = None
    duration_s: float | None = None
    alignment_quality: float | None = None
    interrupt_out: str | None = None
    cutin: dict[str, Any] | None = None
    engagement: dict[str, Any] | None = None


class BlueprintEvidence(BaseModel):
    """Bias-controlled evidence bundle for judge scoring."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    artifact_kind: Literal["blueprint", "triple"]
    source_url: str
    template: str | None = None
    target_duration_s: float | None = None
    hook_banner: str | None = None
    hook_span_quote: str | None = None
    loop_final_span_quote: str | None = None
    engagement_primary: str | None = None
    cta: dict[str, Any] = Field(default_factory=dict)
    beats: list[BeatEvidence]
    engagement_lines: list[str] = Field(default_factory=list)
    cut_ins: list[dict[str, Any]] = Field(default_factory=list)
    planner_rationale: dict[str, Any] = Field(default_factory=dict)
    compile_status: str | None = None
    lint_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class JudgeResult(BaseModel):
    """Raw LLM judge result before it is wrapped in an eval run."""

    model_config = ConfigDict(extra="forbid")

    model: str
    dimensions: dict[DimensionName, DimensionScore]
    aggregate_score: float = Field(ge=0, le=5)
    raw_response: str | None = None


class EvalResult(BaseModel):
    """Persisted eval result for one scored blueprint/artifact case."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    run_id: str
    created_at: str
    case_id: str
    artifact_kind: Literal["blueprint", "triple"]
    source_url: str
    judge_model: str | None = None
    judge_skipped: bool
    gates: PreGateResult
    dimensions: dict[DimensionName, DimensionScore]
    aggregate_score: float = Field(ge=0, le=5)
    planner_rationale: dict[str, Any] = Field(default_factory=dict)
    output_refs: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalDiff(BaseModel):
    """Machine-readable comparison of two timestamped eval runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    created_at: str
    left_run_id: str
    right_run_id: str
    aggregate_delta: float
    dimension_deltas: dict[DimensionName, dict[str, float]]
    gate_changes: dict[str, dict[str, bool]]


def zero_dimension_scores(reason: str) -> dict[DimensionName, DimensionScore]:
    """Return score-zero dimensions without invoking the judge."""

    return {
        dimension: DimensionScore(score=0, rationale=reason)
        for dimension in RETENTION_DIMENSIONS
    }
