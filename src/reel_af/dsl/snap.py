"""Boundary snapping helpers for Composite Transcript DSL v2."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from reel_af.dsl.models import (
    SNAP_TOLERANCE_S,
    DslWord,
    FallbackSegment,
    WordsSidecar,
)

Edge = Literal["head", "tail"]


def sentence_boundaries(words: Sequence[DslWord]) -> list[float]:
    return _unique_sorted(w.end for w in words if w.w.rstrip().endswith((".", "!", "?")))


def word_boundaries(words: Sequence[DslWord]) -> list[float]:
    values: list[float] = []
    for word in words:
        values.append(word.start)
        values.append(word.end)
    return _unique_sorted(values)


def cue_boundaries(segments: Sequence[FallbackSegment]) -> list[float]:
    values: list[float] = []
    for segment in segments:
        values.append(segment.start_s)
        values.append(segment.end_s)
    return _unique_sorted(values)


def snap_edge(
    target: float,
    boundaries: Sequence[float],
    *,
    tol: float | None = None,
    tolerance: float | None = None,
    clamp: tuple[float | None, float | None] | None = None,
    clamp_min: float | None = 0.0,
    clamp_max: float | None = None,
) -> float:
    effective_tol = _effective_tolerance(tol, tolerance)
    lower, upper = _effective_clamp(clamp, clamp_min, clamp_max)

    best = target
    best_dist = float("inf")
    for boundary in boundaries:
        dist = abs(boundary - target)
        if dist > effective_tol:
            continue
        if dist < best_dist or (dist == best_dist and boundary < best):
            best = boundary
            best_dist = dist

    return _clamp(best, lower, upper)


def snap_extend_edge(
    edge: Edge,
    *,
    start_s: float,
    end_s: float,
    duration_s: float,
    sidecar: WordsSidecar,
    tol: float = SNAP_TOLERANCE_S,
    clamp_min: float = 0.0,
    clamp_max: float | None = None,
    previous_end_s: float | None = None,
    next_start_s: float | None = None,
) -> float:
    if duration_s < 0:
        raise ValueError(f"duration_s must be nonnegative, got {duration_s}")

    if edge == "head":
        raw_target = start_s - duration_s
        lower = max(clamp_min, previous_end_s) if previous_end_s is not None else clamp_min
        upper = end_s
    elif edge == "tail":
        raw_target = end_s + duration_s
        lower = start_s
        upper = clamp_max
        if next_start_s is not None:
            upper = min(upper, next_start_s) if upper is not None else next_start_s
    else:
        raise ValueError(f"edge must be 'head' or 'tail', got {edge!r}")

    if raw_target <= lower:
        return lower
    if upper is not None and raw_target >= upper:
        return upper

    snapped = _snap_natural_boundary(raw_target, sidecar, tol=tol)
    return _clamp(snapped, lower, upper)


def _snap_natural_boundary(target: float, sidecar: WordsSidecar, *, tol: float) -> float:
    if sidecar.words:
        snapped = snap_edge(target, sentence_boundaries(sidecar.words), tol=tol, clamp=(None, None))
        if snapped != target:
            return snapped
        return snap_edge(target, word_boundaries(sidecar.words), tol=tol, clamp=(None, None))

    return snap_edge(target, cue_boundaries(sidecar.segments), tol=tol, clamp=(None, None))


def _effective_tolerance(tol: float | None, tolerance: float | None) -> float:
    if tol is not None:
        return tol
    if tolerance is not None:
        return tolerance
    return SNAP_TOLERANCE_S


def _effective_clamp(
    clamp: tuple[float | None, float | None] | None,
    clamp_min: float | None,
    clamp_max: float | None,
) -> tuple[float | None, float | None]:
    if clamp is not None:
        return clamp
    return clamp_min, clamp_max


def _clamp(value: float, lower: float | None, upper: float | None) -> float:
    if lower is not None:
        value = max(value, lower)
    if upper is not None:
        value = min(value, upper)
    return value


def _unique_sorted(values: Iterable[float]) -> list[float]:
    return sorted(set(values))


__all__ = [
    "cue_boundaries",
    "sentence_boundaries",
    "snap_edge",
    "snap_extend_edge",
    "word_boundaries",
]
