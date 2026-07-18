"""Typed planner blueprint models for the A1 producer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reel_af.dsl.models import XfadeEffect

Register = Literal["entertainment", "educational", "b2b"]
Template = Literal[
    "hook_context_value_payoff_cta",
    "problem_agitate_solve",
    "before_after_bridge",
    "myth_bust",
    "listicle",
    "storytime",
]
HookType = Literal["curiosity_gap", "contrarian", "open_loop", "proof", "question", "story"]
BeatRole = Literal["hook", "context", "value", "payoff", "cta"]
InterruptKind = Literal["trans", "join", "black"]
EngagementKind = Literal["send", "save", "share", "comment", "follow", "none"]
CutInKind = Literal["zoom", "visual"]
CtaHardness = Literal["soft", "medium", "hard", "none"]


class PlannerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateSpan(PlannerModel):
    quote: str = Field(min_length=1)
    approx_start_s: float = Field(ge=0)
    approx_end_s: float = Field(gt=0)
    value_score: float = Field(ge=0, le=1)
    emotion: str = ""
    is_claim: bool = False
    payoff_worthy: bool = False

    @model_validator(mode="after")
    def _end_after_start(self) -> "CandidateSpan":
        if self.approx_end_s <= self.approx_start_s:
            raise ValueError("approx_end_s must be greater than approx_start_s")
        return self


class Hook(PlannerModel):
    type: HookType
    banner_line: str = Field(min_length=1)
    span_quote: str = Field(min_length=1)


class Interrupt(PlannerModel):
    kind: InterruptKind
    effect: XfadeEffect | None = None
    dur_s: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _validate_for_kind(self) -> "Interrupt":
        if self.kind == "trans":
            if self.effect is None:
                self.effect = "fade"
            if self.effect == "none" and self.dur_s != 0:
                raise ValueError('trans interrupt with effect="none" requires dur_s=0')
            return self
        if self.kind == "black" and self.dur_s <= 0:
            raise ValueError("black interrupt requires dur_s > 0")
        return self

    @property
    def kind_as_marker(self) -> str:
        if self.kind == "black":
            return "insert"
        return self.kind


class CutIn(PlannerModel):
    type: CutInKind
    at_s: float = Field(ge=0)
    until_s: float = Field(gt=0)
    line: str | None = None
    image_prompt: str | None = None
    zoom_focus: str = "center"

    @model_validator(mode="after")
    def _validate_window_and_payload(self) -> "CutIn":
        if self.until_s <= self.at_s:
            raise ValueError("cut-in until_s must be greater than at_s")
        if self.type == "visual" and not self.image_prompt:
            raise ValueError("visual cut-in requires image_prompt")
        return self


class Engagement(PlannerModel):
    kind: EngagementKind
    line: str | None = None
    primary: bool = True


class Beat(PlannerModel):
    role: BeatRole
    span_quote: str = Field(min_length=1)
    max_len_s: float = Field(gt=0)
    cutin: CutIn | None = None
    interrupt_out: Interrupt | None = None
    engagement: Engagement | None = None


class LoopPlan(PlannerModel):
    strategy: str = Field(min_length=1)
    final_span_quote: str = Field(min_length=1)


class CtaPlan(PlannerModel):
    hardness: CtaHardness
    placements: list[str] = Field(default_factory=list)


class ReelStrategy(PlannerModel):
    template: Template
    target_duration_s: float = Field(gt=0)
    hook: Hook
    engagement_primary: EngagementKind
    cta: CtaPlan


class ReelBlueprint(PlannerModel):
    template: Template
    target_duration_s: float = Field(gt=0)
    hook: Hook
    beats: list[Beat] = Field(min_length=1)
    loop: LoopPlan
    engagement_primary: EngagementKind
    cta: CtaPlan

