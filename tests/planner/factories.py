from __future__ import annotations

from collections.abc import Sequence

from reel_af.planner.models import ArcPlan, DurationPolicy, DurationRange


def duration_policy(
    *,
    advisory_min_s: float | None = 10.0,
    advisory_max_s: float | None = 180.0,
    effective_cap_s: float = 180.0,
    soft_cap_s: float = 180.0,
    cap_overridden: bool = False,
) -> DurationPolicy:
    return DurationPolicy(
        soft_cap_s=soft_cap_s,
        effective_cap_s=effective_cap_s,
        advisory_min_s=advisory_min_s,
        advisory_max_s=advisory_max_s,
        cap_overridden=cap_overridden,
    )


def duration_range(
    *,
    min_s: float = 20.0,
    max_s: float = 35.0,
    rationale: str = "the content is complete inside this range without padding",
) -> DurationRange:
    return DurationRange(min_s=min_s, max_s=max_s, rationale=rationale)


def arc_plan(
    *,
    required_candidate_ids: Sequence[str] = ("c001",),
    optional_candidate_ids: Sequence[str] = (),
    excluded_candidate_ids: Sequence[str] = (),
    completion_criteria: Sequence[str] = (
        "hook establishes the promise",
        "proof explains the mechanism",
        "payoff resolves the hook",
        "loop echoes the hook from a distinct span",
    ),
    promise: str = "the hook promise resolves cleanly",
    thread: str = "one coherent proof thread",
) -> ArcPlan:
    return ArcPlan(
        promise=promise,
        thread=thread,
        completion_criteria=list(completion_criteria),
        required_candidate_ids=list(required_candidate_ids),
        optional_candidate_ids=list(optional_candidate_ids),
        excluded_candidate_ids=list(excluded_candidate_ids),
    )
