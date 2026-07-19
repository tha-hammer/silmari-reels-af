"""Deterministic retention lint rules for the A1 producer."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from reel_af.dsl.models import WordsSidecar
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.models import BeatRole

LintRule = Literal["R1", "R2", "R3", "R4", "R8", "R11", "R12"]
LintSeverity = Literal["warning", "error"]


class LintDiagnostic(BaseModel):
    """One deterministic retention-lint finding."""

    model_config = ConfigDict(extra="forbid")

    rule: LintRule
    severity: LintSeverity
    message: str
    locus: str | None = None


def lint_blueprint(
    blueprint: Any,
    words: WordsSidecar | None = None,
    cfg: PlannerConfig | None = None,
    *,
    resolved: Sequence[Any] | None = None,
    register: str | None = None,
) -> list[LintDiagnostic]:
    """Run deterministic producer retention lint rules R1/R2/R3/R4/R8/R11/R12."""
    cfg = cfg or load_planner_config()
    beats = list(_items(_get(blueprint, "beats", [])))
    findings: list[LintDiagnostic] = []

    findings.extend(_lint_r11(blueprint, cfg))
    findings.extend(_lint_r1(beats, resolved, cfg))
    findings.extend(_lint_r2(beats, resolved, cfg, register or _get(blueprint, "register", None)))
    findings.extend(_lint_r4(beats, resolved, words, cfg))
    findings.extend(_lint_r8(blueprint, beats, cfg))
    findings.extend(_lint_r3(beats, resolved))
    findings.extend(_lint_r12(blueprint, beats))

    return findings


def _diag(rule: LintRule, severity: LintSeverity, message: str, locus: str | None = None):
    return LintDiagnostic(rule=rule, severity=severity, message=message, locus=locus)


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
    beats: Sequence[Any], resolved: Sequence[Any] | None, cfg: PlannerConfig
) -> list[LintDiagnostic]:
    hook_duration = sum(
        duration
        for index, beat in enumerate(beats)
        if _beat_role(beat) is BeatRole.Hook
        for duration in [_duration_s(beat, _resolved_at(resolved, index))]
        if duration is not None
    )
    if hook_duration > cfg.r1_hook_window_s:
        return [
            _diag(
                "R1",
                "warning",
                f"Hook window is {hook_duration:.2f}s, above {cfg.r1_hook_window_s:.2f}s.",
            )
        ]
    return []


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
    if _token_overlap(str(hook_text), str(final_text)) < cfg.r8_min_token_overlap:
        return [
            _diag(
                "R8",
                "warning",
                "Final span does not echo the hook strongly enough.",
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


__all__ = ["LintDiagnostic", "lint_blueprint"]
