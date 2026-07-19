"""Planner serialization helpers for the A1 DSL/composite handoff."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from reel_af.dsl.aligner import align
from reel_af.dsl.models import (
    DSL_HOOKS_WORKFLOW,
    MATCH_QUALITY_FLOOR,
    CutInSpec,
    WordsSidecar,
)
from reel_af.planner.models import (
    CutIn,
    DurationBounds,
    Interrupt,
    XfadeEffect,
    interrupt_marker,
    validate_cut_in,
    validate_interrupt,
)

HOOKS_TARGET = "reel-af.reel_dsl_hooks_to_reels"
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
DEFAULT_DURATION_BOUNDS_S = {"min": 10, "max": 180}


@dataclass(frozen=True)
class ResolvedBeat:
    index: int
    beat: Any
    span_quote: str
    resolved: bool
    start_s: float | None = None
    end_s: float | None = None
    quality: float = 0.0
    reason: str | None = None
    method: str | None = None
    word_range: tuple[int, int] | None = None
    fallback_segment_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class PlannedCutIn:
    """A BAML relative cut-in paired with its resolved containing beat."""

    cut_in: Any
    beat_start_s: float
    beat_end_s: float


def resolve_timecodes(beats: Sequence[Any], words: WordsSidecar) -> list[ResolvedBeat]:
    """Resolve blueprint beat quotes to source time spans via the real DSL aligner."""

    resolved: list[ResolvedBeat] = []
    for index, beat in enumerate(beats):
        quote = str(_get(beat, "span_quote", ""))
        span = align(quote, words)
        if (
            getattr(span, "kind", None) == "aligned"
            and span.quality >= MATCH_QUALITY_FLOOR
        ):
            start_s = float(span.start_s)
            end_s = _clamp_to_max_len(start_s, float(span.end_s), _get(beat, "max_len_s", None))
            resolved.append(
                ResolvedBeat(
                    index=index,
                    beat=beat,
                    span_quote=quote,
                    resolved=True,
                    start_s=start_s,
                    end_s=end_s,
                    quality=float(span.quality),
                    method=span.method,
                    word_range=span.word_range,
                    fallback_segment_range=span.fallback_segment_range,
                )
            )
            continue

        resolved.append(
            ResolvedBeat(
                index=index,
                beat=beat,
                span_quote=quote,
                resolved=False,
                quality=float(getattr(span, "best_quality", 0.0)),
                reason=str(getattr(span, "reason", "below_floor")),
            )
        )
    return resolved


def interrupt_to_marker_text(interrupt: Any) -> str:
    """Render a planner interrupt to a single DSL marker line."""

    if isinstance(interrupt, Interrupt):
        interrupt = validate_interrupt(interrupt)
        marker = interrupt_marker(interrupt)
    else:
        kind = _wire_token(_get(interrupt, "kind"))
        marker = "insert" if kind == "black" else kind
    if marker == "join":
        return "[join]"

    if marker == "insert":
        dur_s = _required_float(_get(interrupt, "dur_s", _get(interrupt, "duration_s", None)), "black duration")
        if dur_s <= 0:
            raise ValueError("black interrupt duration must be positive")
        return f"[insert black {_fmt_num(dur_s)}]"

    if marker == "trans":
        effect = _wire_token(_get(interrupt, "effect", _get(interrupt, "primitive", XfadeEffect.Fade)))
        dur_s = _required_float(_get(interrupt, "dur_s", _get(interrupt, "duration_s", 1.0)), "transition duration")
        if effect == "none" and dur_s != 0.0:
            raise ValueError("effect='none' requires dur_s=0")
        if dur_s == 1.0:
            return f"[trans {effect}]"
        return f"[trans {effect} {_fmt_num(dur_s)}]"

    raise ValueError(f"unsupported interrupt kind: {marker!r}")


def serialize_composite(blueprint: Any, resolved: Sequence[ResolvedBeat]) -> str:
    """Serialize a resolved blueprint into `.ts.md` text readable by the real DSL."""

    beats = list(_get(blueprint, "beats", []))
    if len(beats) != len(resolved):
        raise ValueError(
            f"resolved beat count mismatch: expected {len(beats)}, got {len(resolved)}"
        )

    lines: list[str] = []
    for beat, item in zip(beats, resolved, strict=True):
        if not item.resolved or item.start_s is None:
            raise ValueError(f"unresolved beat at index {item.index}: {item.reason}")
        quote = str(_get(beat, "span_quote", item.span_quote)).strip()
        if not quote:
            raise ValueError(f"empty beat quote at index {item.index}")
        lines.append(f"{_fmt_ts(item.start_s)}  {quote}")

        interrupt = _get(beat, "interrupt_out", None)
        if interrupt is not None:
            lines.append(interrupt_to_marker_text(interrupt))

    return "\n".join(lines) + "\n"


def build_hook_plan(
    source_url: str,
    hook: Any,
    span: ResolvedBeat | Sequence[ResolvedBeat],
    cut_ins: Sequence[Any],
    composite_ref: str,
    source_id: str | None = None,
    model: str = DEFAULT_MODEL,
    duration_bounds_s: Mapping[str, float] | None = None,
    idx: int = 1,
    title: str | None = None,
    idea: str | None = None,
) -> dict[str, Any]:
    """Build the hook-plan JSON consumed by `reel_dsl_hooks_to_reels`."""

    span = _first_resolved_span(span)
    if not composite_ref:
        raise ValueError("composite_ref is required")
    if not span.resolved or span.start_s is None or span.end_s is None:
        raise ValueError(f"hook span is unresolved: {span.reason}")

    normalized_bounds = _duration_bounds(duration_bounds_s)
    source_id = source_id or _source_id_from_url(source_url)
    hook_text = str(_get(hook, "span_quote", span.span_quote)).strip()
    banner = str(_get(hook, "banner_line", hook_text)).strip()
    clip_title = title or _slug_title(banner or hook_text)
    clip_idea = idea or str(_get(hook, "idea", banner or hook_text)).strip()
    cut_in_payloads = [_cut_in_payload(cut_in, span=span) for cut_in in cut_ins]
    idempotency_key = _idempotency_key(
        source_url=source_url,
        source_id=source_id,
        idx=idx,
        start_s=span.start_s,
        end_s=span.end_s,
        composite_ref=composite_ref,
    )

    return {
        "schema_version": "1",
        "workflow": DSL_HOOKS_WORKFLOW,
        "source_url": source_url,
        "source_id": source_id,
        "model": model,
        "duration_bounds_s": normalized_bounds,
        "clips": [
            {
                "idx": idx,
                "title": clip_title,
                "idea": clip_idea,
                "hook": banner or hook_text,
                "start_s": span.start_s,
                "end_s": span.end_s,
                "excerpt": hook_text,
                "composite_ref": composite_ref,
                "target": HOOKS_TARGET,
                "idempotency_key": idempotency_key,
                "cut_ins": cut_in_payloads,
            }
        ],
    }


def _first_resolved_span(span: ResolvedBeat | Sequence[ResolvedBeat]) -> ResolvedBeat:
    if isinstance(span, ResolvedBeat):
        return span
    if not span:
        raise ValueError("at least one resolved beat is required")
    return span[0]


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _clamp_to_max_len(start_s: float, end_s: float, max_len_s: Any) -> float:
    if max_len_s is None:
        return end_s
    max_len = float(max_len_s)
    if max_len <= 0:
        return end_s
    return min(end_s, start_s + max_len)


def _required_float(value: Any, label: str) -> float:
    if value is None:
        raise ValueError(f"{label} is required")
    return float(value)


def _fmt_ts(seconds: float) -> str:
    total_ms = int(round(float(seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _fmt_num(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _source_id_from_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        candidate = parsed.path.strip("/").split("/", 1)[0]
        if candidate:
            return candidate
    if "youtube.com" in host:
        video_id = parse_qs(parsed.query).get("v", [None])[0]
        if video_id:
            return video_id
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
    return digest


def _duration_bounds(bounds: Mapping[str, float] | DurationBounds | None) -> dict[str, float]:
    if bounds is None:
        return dict(DEFAULT_DURATION_BOUNDS_S)
    min_s = _get(bounds, "min", _get(bounds, "min_s", DEFAULT_DURATION_BOUNDS_S["min"]))
    max_s = _get(bounds, "max", _get(bounds, "max_s", DEFAULT_DURATION_BOUNDS_S["max"]))
    return {"min": min_s, "max": max_s}


def _cut_in_payload(cut_in: Any, *, span: ResolvedBeat) -> dict[str, Any]:
    beat_start_s = span.start_s
    beat_end_s = span.end_s
    if isinstance(cut_in, PlannedCutIn):
        beat_start_s = cut_in.beat_start_s
        beat_end_s = cut_in.beat_end_s
        cut_in = cut_in.cut_in

    if isinstance(cut_in, CutIn):
        raw = validate_cut_in(cut_in).model_dump(exclude_none=True)
    elif hasattr(cut_in, "model_dump"):
        raw = cut_in.model_dump(exclude_none=True)
    elif isinstance(cut_in, Mapping):
        raw = dict(cut_in)
    else:
        raw = {
            key: getattr(cut_in, key)
            for key in (
                "type",
                "offset_s",
                "dur_s",
                "at_s",
                "until_s",
                "line",
                "image_prompt",
                "zoom_focus",
            )
            if hasattr(cut_in, key)
        }
    raw = {key: value for key, value in raw.items() if value is not None}
    if "type" in raw:
        raw["type"] = _wire_token(raw["type"])
    offset_s = raw.pop("offset_s", None)
    dur_s = raw.pop("dur_s", None)
    if "at_s" not in raw and offset_s is not None:
        raw["at_s"] = float(beat_start_s or 0.0) + float(offset_s)
    if "until_s" not in raw and offset_s is not None and dur_s is not None:
        until_s = float(raw["at_s"]) + float(dur_s)
        if beat_end_s is not None:
            until_s = min(until_s, float(beat_end_s))
        raw["until_s"] = until_s
    return CutInSpec.model_validate(raw).model_dump(
        exclude_none=True,
        exclude_defaults=True,
    )


def _wire_token(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    text = str(value)
    aliases = {
        "Black": "black",
        "Join": "join",
        "Trans": "trans",
        "Zoom": "zoom",
        "Visual": "visual",
        "NoEffect": "none",
        "NoEngagement": "none",
        "NoCta": "none",
    }
    return aliases.get(text, text.lower())


def _slug_title(text: str) -> str:
    words = [part.strip(".,:;!?").lower() for part in text.split()]
    words = [word for word in words if word]
    return " ".join(words[:8]) or "planned hook"


def _idempotency_key(
    *,
    source_url: str,
    source_id: str,
    idx: int,
    start_s: float,
    end_s: float,
    composite_ref: str,
) -> str:
    raw = f"{source_url}|{idx}|{start_s:.3f}|{end_s:.3f}|{composite_ref}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"a1:{source_id}:{digest}:clip:{idx}"


__all__ = [
    "DEFAULT_DURATION_BOUNDS_S",
    "DEFAULT_MODEL",
    "HOOKS_TARGET",
    "PlannedCutIn",
    "ResolvedBeat",
    "build_hook_plan",
    "interrupt_to_marker_text",
    "resolve_timecodes",
    "serialize_composite",
]
