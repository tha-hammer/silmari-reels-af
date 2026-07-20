"""Planner domain type facade backed by generated BAML types."""

from __future__ import annotations

from enum import Enum
from typing import Literal, TypeVar

from baml_client.types import (
    ArcPlan,
    Beat,
    BeatRole,
    CandidateSpan,
    CtaHardness,
    CtaPlan,
    CutIn,
    CutInKind,
    DurationBounds,
    DurationPolicy,
    DurationRange,
    Engagement,
    EngagementKind,
    Hook,
    HookType,
    Interrupt,
    InterruptKind,
    LoopPlan,
    PlannerCandidate,
    ReelBlueprint,
    ReelStrategy,
    Template,
    XfadeEffect,
)

Register = Literal["entertainment", "educational", "b2b"]

TInterrupt = TypeVar("TInterrupt", bound=Interrupt)

# Joined span_quote values preserve the existing one-id BAML schema by carrying
# the first source-time PlannerCandidate identity. Deterministic validation may
# only extend from that candidate into later adjacent candidate word ranges.
JOINED_SPAN_IDENTITY_POLICY = "first_adjacent_candidate"


def interrupt_marker(interrupt: Interrupt) -> str:
    """Return the DSL marker name for a BAML interrupt."""

    kind = _enum_value(interrupt.kind)
    if kind in {"Black", "black"}:
        return "insert"
    if kind in {"Trans", "trans"}:
        return "trans"
    if kind in {"Join", "join"}:
        return "join"
    raise ValueError(f"unsupported interrupt kind: {kind!r}")


def validate_candidate_span(span: CandidateSpan) -> CandidateSpan:
    """Validate semantic candidate span invariants not enforced by BAML SAP."""

    if (
        span.approx_start_s is not None
        and span.approx_end_s is not None
        and span.approx_end_s <= span.approx_start_s
    ):
        raise ValueError("approx_end_s must be greater than approx_start_s")
    return span


def validate_cut_in(cut_in: CutIn) -> CutIn:
    """Validate semantic cut-in invariants not enforced by BAML SAP."""

    if cut_in.offset_s < 0:
        raise ValueError("cut-in offset_s must be non-negative")
    if cut_in.dur_s <= 0:
        raise ValueError("cut-in duration must be positive")
    if _enum_value(cut_in.type) in {"Visual", "visual"} and not cut_in.image_prompt:
        raise ValueError("visual cut-in requires image_prompt")
    return cut_in


def validate_interrupt(interrupt: TInterrupt) -> TInterrupt:
    """Validate and normalize semantic interrupt invariants."""

    kind = _enum_value(interrupt.kind)
    if kind in {"Trans", "trans"}:
        effect = interrupt.effect or XfadeEffect.Fade
        dur_s = 0.0 if interrupt.dur_s is None else float(interrupt.dur_s)
        if _enum_value(effect) in {"NoEffect", "none"} and dur_s != 0.0:
            raise ValueError('trans interrupt with effect="none" requires dur_s=0')
        if effect is not interrupt.effect or dur_s != interrupt.dur_s:
            return interrupt.model_copy(update={"effect": effect, "dur_s": dur_s})
        return interrupt

    if kind in {"Black", "black"}:
        if interrupt.dur_s is None or interrupt.dur_s <= 0:
            raise ValueError("black interrupt requires dur_s > 0")
    return interrupt


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


__all__ = [
    "ArcPlan",
    "Beat",
    "BeatRole",
    "CandidateSpan",
    "CtaHardness",
    "CtaPlan",
    "CutIn",
    "CutInKind",
    "DurationBounds",
    "DurationPolicy",
    "DurationRange",
    "Engagement",
    "EngagementKind",
    "Hook",
    "HookType",
    "Interrupt",
    "InterruptKind",
    "JOINED_SPAN_IDENTITY_POLICY",
    "LoopPlan",
    "PlannerCandidate",
    "ReelBlueprint",
    "ReelStrategy",
    "Register",
    "Template",
    "XfadeEffect",
    "interrupt_marker",
    "validate_candidate_span",
    "validate_cut_in",
    "validate_interrupt",
]
