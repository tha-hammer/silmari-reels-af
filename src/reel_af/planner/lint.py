"""Deterministic retention lint rules for the A1 producer."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from reel_af.dsl.models import FADE_TO_COLOR_EFFECTS, WordsSidecar
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.models import BeatRole

LintRule = Literal["R1", "R2", "R3", "R4", "R7", "R8", "R11", "R12"]
LintSeverity = Literal["warning", "error"]


class LintDiagnostic(BaseModel):
    """One deterministic retention-lint finding."""

    model_config = ConfigDict(extra="forbid")

    rule: LintRule
    severity: LintSeverity
    message: str
    locus: str | None = None
    context: dict[str, Any] | None = None


def lint_blueprint(
    blueprint: Any,
    words: WordsSidecar | None = None,
    cfg: PlannerConfig | None = None,
    *,
    resolved: Sequence[Any] | None = None,
    register: str | None = None,
    duration_policy: Any | None = None,
    strategy: Any | None = None,
    candidates: Sequence[Any] | None = None,
) -> list[LintDiagnostic]:
    """Run deterministic producer retention lint rules R1/R2/R3/R4/R7/R8/R11/R12."""
    cfg = cfg or load_planner_config()
    beats = list(_items(_get(blueprint, "beats", [])))
    findings: list[LintDiagnostic] = []

    findings.extend(_lint_r11(blueprint, cfg))
    findings.extend(_lint_r1(beats, resolved, cfg, candidates))
    findings.extend(_lint_r2(beats, resolved, cfg, register or _get(blueprint, "register", None)))
    findings.extend(_lint_r4(beats, resolved, words, cfg))
    findings.extend(_lint_r8(blueprint, beats, cfg))
    findings.extend(_lint_r7(blueprint, beats, resolved, cfg, duration_policy, strategy, candidates))
    findings.extend(_lint_r3(beats, resolved))
    findings.extend(_lint_r12(blueprint, beats))

    return findings


def _diag(
    rule: LintRule,
    severity: LintSeverity,
    message: str,
    locus: str | None = None,
    context: dict[str, Any] | None = None,
) -> LintDiagnostic:
    return LintDiagnostic(
        rule=rule,
        severity=severity,
        message=message,
        locus=locus,
        context=context,
    )


def _lint_r11(blueprint: Any, cfg: PlannerConfig) -> list[LintDiagnostic]:
    text = " \n".join(_display_texts(blueprint))
    if not text:
        return []
    for pattern in cfg.r11_bait_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return [
                _diag(
                    "R11",
                    "error",
                    "Engagement-bait language is not allowed in hook or CTA text.",
                )
            ]
    return []


def _lint_r1(
    beats: Sequence[Any],
    resolved: Sequence[Any] | None,
    cfg: PlannerConfig,
    candidates: Sequence[Any] | None = None,
) -> list[LintDiagnostic]:
    hook_duration = sum(
        duration
        for index, beat in enumerate(beats)
        if _beat_role(beat) is BeatRole.Hook
        for duration in [_duration_s(beat, _resolved_at(resolved, index))]
        if duration is not None
    )
    if hook_duration > cfg.r1_hook_window_s:
        # AF-10e: a hook the planner JOINED past its own candidate span is the
        # planner's optional choice — over the window it is an error (repair:
        # trim to the candidate span or don't join). A natural single-span hook
        # over the window keeps the advisory warning.
        if _hook_joined_past_candidate(beats, resolved, candidates):
            return [
                _diag(
                    "R1",
                    "error",
                    f"Hook window is {hook_duration:.2f}s, above "
                    f"{cfg.r1_hook_window_s:.2f}s, via span-join: keep the hook "
                    f"within its own candidate span or only join when the result "
                    f"stays within the window.",
                )
            ]
        return [
            _diag(
                "R1",
                "warning",
                f"Hook window is {hook_duration:.2f}s, above {cfg.r1_hook_window_s:.2f}s.",
            )
        ]
    return []


def _hook_joined_past_candidate(
    beats: Sequence[Any],
    resolved: Sequence[Any] | None,
    candidates: Sequence[Any] | None,
) -> bool:
    """True when a hook beat's resolved word range extends beyond its own
    candidate's word range — i.e. the span-join policy stretched the hook."""
    if not candidates or resolved is None:
        return False
    ranges: dict[tuple[str, int], tuple[int, int]] = {}
    for candidate in _items(candidates):
        candidate_id = _get(candidate, "candidate_id", None)
        word_range = _get(candidate, "word_range", None)
        if not candidate_id or word_range is None:
            continue
        occurrence = int(_get(candidate, "occurrence_index", 0) or 0)
        ranges[(str(candidate_id), occurrence)] = (int(word_range[0]), int(word_range[1]))
    for index, beat in enumerate(beats):
        if _beat_role(beat) is not BeatRole.Hook:
            continue
        item = _resolved_at(resolved, index)
        beat_range = _get(item, "word_range", None)
        candidate_id = _get(beat, "candidate_id", None)
        if beat_range is None or not candidate_id:
            continue
        occurrence = int(_get(beat, "occurrence_index", 0) or 0)
        candidate_range = ranges.get((str(candidate_id), occurrence))
        if candidate_range is not None and int(beat_range[1]) > candidate_range[1]:
            return True
    return False


def _lint_r2(
    beats: Sequence[Any],
    resolved: Sequence[Any] | None,
    cfg: PlannerConfig,
    register: str | None,
) -> list[LintDiagnostic]:
    cadence = cfg.r2_cadence_s.get(register or cfg.default_register, cfg.r2_cadence_s[cfg.default_register])
    findings: list[LintDiagnostic] = []
    for index, beat in enumerate(beats):
        duration = _duration_s(beat, _resolved_at(resolved, index))
        if duration is not None and duration > cadence and not _has_adjacent_change(beat):
            findings.append(
                _diag(
                    "R2",
                    "warning",
                    f"Beat {index} runs {duration:.2f}s without an adjacent change.",
                    locus=f"beat[{index}]",
                )
            )
    return findings


def _lint_r4(
    beats: Sequence[Any],
    resolved: Sequence[Any] | None,
    words: WordsSidecar | None,
    cfg: PlannerConfig,
) -> list[LintDiagnostic]:
    if words is None or not words.words:
        return []
    findings: list[LintDiagnostic] = []
    for index, beat in enumerate(beats):
        span = _span_s(beat, _resolved_at(resolved, index))
        if span is None:
            continue
        covered = [
            word
            for word in words.words
            if word.start >= span[0] and word.end <= span[1]
        ]
        for left, right in zip(covered, covered[1:]):
            gap = right.start - left.end
            if gap > cfg.r4_max_gap_s:
                findings.append(
                    _diag(
                        "R4",
                        "warning",
                        f"Beat {index} contains a {gap:.2f}s internal word gap.",
                        locus=f"beat[{index}]",
                    )
                )
                break
    return findings


def _lint_r8(blueprint: Any, beats: Sequence[Any], cfg: PlannerConfig) -> list[LintDiagnostic]:
    hook = _get(blueprint, "hook", {})
    hook_text = _get(hook, "span_quote", None) or _first_role_quote(beats, BeatRole.Hook)
    loop = _get(blueprint, "loop", {})
    final_text = _get(loop, "final_span_quote", None)
    if final_text is None and beats:
        final_text = _get(beats[-1], "span_quote", None)
    if not hook_text or not final_text:
        return []
    # AF-9zs: the loop tie-back is MANDATORY for every strategy (a PAS run
    # shipped without echoing its hook). It is satisfied by EITHER the loop
    # closing on the hook candidate's own material (loop.candidate_id ==
    # hook.candidate_id — "echoing the hook candidate") OR a textual echo at
    # the configured overlap. Neither → error, repaired in the pipeline.
    hook_candidate = _get(hook, "candidate_id", None)
    loop_candidate = _get(loop, "candidate_id", None)
    if hook_candidate and loop_candidate and str(hook_candidate) == str(loop_candidate):
        return []
    if _token_overlap(str(hook_text), str(final_text)) < cfg.r8_min_token_overlap:
        return [
            _diag(
                "R8",
                "error",
                "Final span does not echo the hook: tie the loop to the hook "
                "candidate or echo its key tokens.",
            )
        ]
    return []


def _lint_r3(beats: Sequence[Any], resolved: Sequence[Any] | None) -> list[LintDiagnostic]:
    durations = [
        duration
        for index, beat in enumerate(beats)
        for duration in [_duration_s(beat, _resolved_at(resolved, index))]
        if duration is not None
    ]
    if len(durations) >= 6:
        return _lint_r3_sectional(beats, durations)
    back_half = durations[len(durations) // 2 :]
    if len(back_half) < 2:
        return []
    if any(curr >= prev for prev, curr in zip(back_half, back_half[1:])):
        return [
            _diag(
                "R3",
                "warning",
                "Back-half beat durations should tighten toward the payoff.",
            )
        ]
    return []


def _lint_r3_sectional(beats: Sequence[Any], durations: Sequence[float]) -> list[LintDiagnostic]:
    third = max(1, len(durations) // 3)
    middle = durations[third : third * 2] or durations[third:]
    payoff_approach = durations[third * 2 :] or durations[-third:]
    if not middle or not payoff_approach:
        return []
    middle_change_density = _change_density(beats[third : third * 2])
    payoff_change_density = _change_density(beats[third * 2 :])
    if _median(payoff_approach) <= _median(middle):
        return []
    if payoff_change_density > middle_change_density:
        return []
    return [
        _diag(
            "R3",
            "warning",
            "Long-reel payoff approach should tighten by duration or higher change density.",
        )
    ]


def _lint_r7(
    blueprint: Any,
    beats: Sequence[Any],
    resolved: Sequence[Any] | None,
    cfg: PlannerConfig,
    duration_policy: Any | None,
    strategy: Any | None,
    candidates: Sequence[Any] | None,
) -> list[LintDiagnostic]:
    policy = duration_policy or _get(blueprint, "duration_policy", None)
    arc = _get(strategy, "arc", None) or _get(blueprint, "arc", None)
    if policy is None and arc is None and not _get(blueprint, "completion_rationale", None):
        return []

    findings: list[LintDiagnostic] = []
    if policy is not None:
        total_duration_s = estimate_blueprint_duration_s(beats, resolved)
        effective_cap_s = float(_get(policy, "effective_cap_s", cfg.r7_soft_cap_s))
        tolerance_s = float(cfg.r7_cap_tolerance_s)
        if total_duration_s > effective_cap_s + tolerance_s:
            findings.append(
                _diag(
                    "R7",
                    "error",
                    (
                        f"Blueprint duration {total_duration_s:.2f}s exceeds active "
                        f"cap {effective_cap_s:.2f}s."
                    ),
                    context={
                        "total_duration_s": total_duration_s,
                        "effective_cap_s": effective_cap_s,
                        "cap_overridden": bool(_get(policy, "cap_overridden", False)),
                        "advisory_min_s": _get(policy, "advisory_min_s", None),
                        "advisory_max_s": _get(policy, "advisory_max_s", None),
                    },
                )
            )
        advisory_min_s = _get(policy, "advisory_min_s", None)
        if advisory_min_s is not None and total_duration_s < float(advisory_min_s):
            findings.append(
                _diag(
                    "R7",
                    "warning",
                    (
                        f"Blueprint duration {total_duration_s:.2f}s is below advisory "
                        f"minimum {float(advisory_min_s):.2f}s; this is allowed only "
                        "when completion_rationale shows the arc is complete."
                    ),
                    context={"total_duration_s": total_duration_s, "advisory_min_s": advisory_min_s},
                )
            )
        advisory_max_s = _get(policy, "advisory_max_s", None)
        if (
            advisory_max_s is not None
            and total_duration_s > float(advisory_max_s)
            and total_duration_s <= effective_cap_s + tolerance_s
        ):
            findings.append(
                _diag(
                    "R7",
                    "warning",
                    (
                        f"Blueprint duration {total_duration_s:.2f}s exceeds advisory "
                        f"maximum {float(advisory_max_s):.2f}s but remains under active cap."
                    ),
                    context={"total_duration_s": total_duration_s, "advisory_max_s": advisory_max_s},
                )
            )

    if len(beats) > cfg.max_beats:
        findings.append(
            _diag(
                "R7",
                "error",
                f"Blueprint has {len(beats)} beats, above max_beats={cfg.max_beats}.",
                context={"beat_count": len(beats), "max_beats": cfg.max_beats},
            )
        )

    findings.extend(validate_arc_completion(strategy or blueprint, blueprint, candidates or []))
    findings.extend(_lint_beat_completion_roles(beats, strategy or blueprint))
    return findings


def estimate_blueprint_duration_s(
    beats: Sequence[Any],
    resolved: Sequence[Any] | None = None,
) -> float:
    total = 0.0
    for index, beat in enumerate(beats):
        duration = _duration_s(beat, _resolved_at(resolved, index))
        if duration is not None:
            total += max(0.0, float(duration))
        interrupt = _get(beat, "interrupt_out", None)
        if interrupt is None:
            continue
        kind = _wire_token(_get(interrupt, "kind", ""))
        dur_s = float(_get(interrupt, "dur_s", _get(interrupt, "duration_s", 0.0)) or 0.0)
        if kind == "black":
            total += dur_s
        elif kind == "trans":
            effect = _wire_token(_get(interrupt, "effect", "fade"))
            if effect != "none" and effect not in FADE_TO_COLOR_EFFECTS:
                total -= dur_s
    return max(0.0, total)


def validate_arc_completion(
    strategy: Any,
    blueprint: Any,
    candidates: Sequence[Any],
) -> list[LintDiagnostic]:
    arc = _get(strategy, "arc", None) or _get(blueprint, "arc", None)
    if arc is None:
        return [_diag("R7", "error", "ArcPlan is required for content-driven length.")]

    criteria = [str(item).strip() for item in (_get(arc, "completion_criteria", []) or []) if str(item).strip()]
    required_ids = [str(item).strip() for item in (_get(arc, "required_candidate_ids", []) or []) if str(item).strip()]
    if not str(_get(arc, "promise", "") or "").strip():
        return [_diag("R7", "error", "ArcPlan promise is required.")]
    if not str(_get(arc, "thread", "") or "").strip():
        return [_diag("R7", "error", "ArcPlan thread is required.")]
    if not criteria:
        return [_diag("R7", "error", "ArcPlan completion_criteria are required.")]
    if not required_ids:
        return [_diag("R7", "error", "ArcPlan required_candidate_ids are required.")]

    findings: list[LintDiagnostic] = []
    beats = list(_items(_get(blueprint, "beats", [])))
    beat_candidate_ids = {str(_get(beat, "candidate_id", "")) for beat in beats if _get(beat, "candidate_id", None)}
    omitted_ids = {str(item) for item in (_get(blueprint, "omitted_candidate_ids", []) or [])}
    cap_rationale = str(_get(blueprint, "cap_rationale", "") or "").strip()
    missing_ids = sorted(set(required_ids) - beat_candidate_ids)
    missing_without_cap = [candidate_id for candidate_id in missing_ids if candidate_id not in omitted_ids]
    if missing_without_cap:
        findings.append(
            _diag(
                "R7",
                "error",
                "Blueprint omits required arc candidates: " + ", ".join(missing_without_cap),
                context={"missing_candidate_ids": missing_without_cap},
            )
        )
    omitted_required = sorted(set(required_ids) & omitted_ids)
    if omitted_required and not cap_rationale:
        findings.append(
            _diag(
                "R7",
                "error",
                "Required arc candidates may be omitted only with cap_rationale.",
                context={"omitted_required_candidate_ids": omitted_required},
            )
        )

    completion_rationale = str(_get(blueprint, "completion_rationale", "") or "").strip()
    if not completion_rationale:
        findings.append(_diag("R7", "error", "Blueprint completion_rationale is required."))
    elif not _mentions_completion_criteria(completion_rationale, criteria):
        findings.append(
            _diag(
                "R7",
                "warning",
                "completion_rationale should reference the declared completion criteria.",
            )
        )

    roles = {_beat_role(beat) for beat in beats}
    for required_role in (BeatRole.Hook, BeatRole.Payoff):
        if required_role not in roles:
            findings.append(
                _diag("R7", "error", f"Blueprint missing required {required_role.value} beat.")
            )
    final_quote = str(_get(_get(blueprint, "loop", {}), "final_span_quote", "") or "").strip()
    last_quote = str(_get(beats[-1], "span_quote", "") or "").strip() if beats else ""
    if final_quote and last_quote and final_quote != last_quote:
        findings.append(_diag("R7", "error", "Loop final_span_quote must match final beat."))
    known_ids = {str(_get(candidate, "candidate_id", "")) for candidate in candidates}
    unknown_required = sorted(set(required_ids) - known_ids) if known_ids else []
    if unknown_required:
        findings.append(
            _diag(
                "R7",
                "error",
                "ArcPlan references unknown required candidates: " + ", ".join(unknown_required),
            )
        )
    return findings


def _lint_beat_completion_roles(beats: Sequence[Any], strategy: Any) -> list[LintDiagnostic]:
    arc = _get(strategy, "arc", None)
    if arc is None:
        return []
    required_ids = {str(item) for item in (_get(arc, "required_candidate_ids", []) or [])}
    optional_ids = {str(item) for item in (_get(arc, "optional_candidate_ids", []) or [])}
    findings: list[LintDiagnostic] = []
    for index, beat in enumerate(beats):
        if _get(beat, "completion_role", None) or _get(beat, "completion_criterion_ids", None):
            continue
        candidate_id = str(_get(beat, "candidate_id", "") or "")
        role = _beat_role(beat)
        if candidate_id in required_ids or role in {BeatRole.Hook, BeatRole.Payoff, BeatRole.Cta}:
            continue
        if candidate_id in optional_ids:
            findings.append(
                _diag(
                    "R7",
                    "warning",
                    "Optional beat should declare the completion role it supports.",
                    locus=f"beat[{index}]",
                )
            )
            continue
        findings.append(
            _diag(
                "R7",
                "warning",
                "Beat should satisfy a criterion, bridge criteria, or add non-duplicative proof.",
                locus=f"beat[{index}]",
            )
        )
    return findings


def _lint_r12(blueprint: Any, beats: Sequence[Any]) -> list[LintDiagnostic]:
    cta = _get(blueprint, "cta", {})
    explicit_count = _get(cta, "primary_count", None)
    if explicit_count is not None:
        count = int(explicit_count)
    else:
        placements = list(_items(_get(cta, "placements", [])))
        role_count = sum(1 for beat in beats if _beat_role(beat) is BeatRole.Cta)
        count = max(len(placements), role_count)
    if count > 1:
        return [_diag("R12", "warning", "Blueprint has more than one primary CTA.")]
    return []


def _duration_s(beat: Any, resolved: Any | None) -> float | None:
    duration = _get(resolved, "duration_s", None)
    if duration is not None:
        return float(duration)
    span = _span_s(beat, resolved)
    if span is not None:
        return span[1] - span[0]
    for key in ("duration_s", "dur_s", "max_len_s"):
        value = _get(beat, key, None)
        if value is not None:
            return float(value)
    return None


def _span_s(beat: Any, resolved: Any | None) -> tuple[float, float] | None:
    start = _get(resolved, "start_s", None)
    end = _get(resolved, "end_s", None)
    if start is None or end is None:
        start = _get(beat, "start_s", None)
        end = _get(beat, "end_s", None)
    if start is None or end is None:
        return None
    return float(start), float(end)


def _resolved_at(resolved: Sequence[Any] | None, index: int) -> Any | None:
    if resolved is None or index >= len(resolved):
        return None
    return resolved[index]


def _has_adjacent_change(beat: Any) -> bool:
    return any(
        _get(beat, key, None) is not None
        for key in ("interrupt_out", "cutin", "cut_in")
    )


def _first_role_quote(beats: Sequence[Any], role: BeatRole) -> str | None:
    for beat in beats:
        if _beat_role(beat) is role:
            quote = _get(beat, "span_quote", None)
            if quote:
                return str(quote)
    return None


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _mentions_completion_criteria(rationale: str, criteria: Sequence[str]) -> bool:
    rationale_tokens = set(_tokens(rationale))
    if not rationale_tokens:
        return False
    for criterion in criteria:
        criterion_tokens = set(_tokens(criterion))
        if criterion_tokens and len(rationale_tokens & criterion_tokens) >= min(2, len(criterion_tokens)):
            return True
    return False


def _change_density(beats: Sequence[Any]) -> float:
    if not beats:
        return 0.0
    changed = sum(1 for beat in beats if _has_adjacent_change(beat))
    return changed / len(beats)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _wire_token(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    aliases = {
        "Black": "black",
        "Join": "join",
        "Trans": "trans",
        "Zoom": "zoom",
        "Visual": "visual",
        "NoEffect": "none",
        "Dissolve": "dissolve",
        "Smoothleft": "smoothleft",
        "Smoothright": "smoothright",
        "Smoothup": "smoothup",
        "Smoothdown": "smoothdown",
        "Hblur": "hblur",
        "Circleopen": "circleopen",
        "Radial": "radial",
        "Pixelize": "pixelize",
        "Fadeblack": "fadeblack",
        "Fadewhite": "fadewhite",
        "Fade": "fade",
    }
    text = str(value)
    return aliases.get(text, text.lower())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _items(value: Any) -> Iterable[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return value
    return [value]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _beat_role(beat: Any) -> BeatRole | None:
    value = _get(beat, "role", None)
    if isinstance(value, BeatRole):
        return value
    if isinstance(value, Enum):
        value = value.value
    mapping = {
        "Hook": BeatRole.Hook,
        "hook": BeatRole.Hook,
        "Context": BeatRole.Context,
        "context": BeatRole.Context,
        "Value": BeatRole.Value,
        "value": BeatRole.Value,
        "Payoff": BeatRole.Payoff,
        "payoff": BeatRole.Payoff,
        "Cta": BeatRole.Cta,
        "cta": BeatRole.Cta,
    }
    return mapping.get(str(value))


def _display_texts(blueprint: Any) -> Iterable[str]:
    hook = _get(blueprint, "hook", {})
    banner = _get(hook, "banner_line", None)
    if banner:
        yield str(banner)

    cta = _get(blueprint, "cta", {})
    for key in ("line", "text", "copy"):
        value = _get(cta, key, None)
        if value:
            yield str(value)

    for beat in _items(_get(blueprint, "beats", [])):
        engagement = _get(beat, "engagement", None)
        line = _get(engagement, "line", None)
        if line:
            yield str(line)


__all__ = [
    "LintDiagnostic",
    "estimate_blueprint_duration_s",
    "lint_blueprint",
    "validate_arc_completion",
]
