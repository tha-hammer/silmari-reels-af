"""A1 hook-plan cut-ins -> validated ``render.overlays.CutInOverlay`` (B9a).

This module is the consumer wiring for the otherwise-dormant ``overlays.py``
metadata model. It is PURE: it validates and types cut-ins, and returns
diagnostics. It renders nothing and touches no filesystem.

Time base
---------
Both sides are ABSOLUTE SOURCE TIME. ``CutInOverlay.at_s``/``until_s`` are
absolute — ``overlays._relative_window`` derives segment-relative windows by
subtracting ``segment_start_s``, and clamps partial overlaps to the segment. So
the mapping is identity on the time fields; converting here would double-apply
the library's own arithmetic.

Rejection policy
----------------
A cut-in that overlaps NO source segment is rejected with a typed
``CUTIN_INVALID`` diagnostic. Today such a cut-in is silently dropped by
``overlays.build_overlay_filtergraph`` (it filters on ``_relative_window(...) is
not None``), so the reel renders without it and nothing reports the loss.

A cut-in that SPANS a segment boundary is NOT rejected: the library clamps it to
each overlapping segment by design, which is a defined, deterministic rendering.

Overlay RENDERING (B9b) is a separate follow-up build and is not wired here.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from reel_af.dsl.models import CutInSpec, Diagnostic, FootageReel
from reel_af.render.overlays import CutInOverlay

__all__ = ["map_cut_ins"]


def _coerce_spec(cut_in: CutInSpec | CutInOverlay | Mapping[str, Any]) -> CutInSpec:
    if isinstance(cut_in, CutInSpec):
        return cut_in
    if isinstance(cut_in, CutInOverlay):
        return CutInSpec.model_validate(cut_in.model_dump())
    return CutInSpec.model_validate(cut_in)


def _source_spans(reel: FootageReel) -> list[tuple[float, float]]:
    """Absolute [start_s, end_s) spans of the reel's SOURCE segments.

    Black segments carry no source footage, so they anchor no cut-in.
    """

    return [
        (segment.start_s, segment.end_s)
        for segment in reel.segments
        if getattr(segment, "kind", None) == "source"
    ]


def _overlaps_any_span(spec: CutInSpec, spans: list[tuple[float, float]]) -> bool:
    """Pure question: does this cut-in intersect any source span?

    Half-open intersection, mirroring _relative_window's ``end_s <= start_s ->
    None`` rule: a zero-width intersection is not an overlap.
    """

    return any(spec.at_s < end_s and spec.until_s > start_s for start_s, end_s in spans)


def _invalid(message: str, cut_in: Any) -> Diagnostic:
    return Diagnostic(
        code="CUTIN_INVALID",
        message=message,
        severity="error",
        context={"cut_in": repr(cut_in)},
    )


def map_cut_ins(
    cut_ins: Iterable[CutInSpec | CutInOverlay | Mapping[str, Any]],
    *,
    reel: FootageReel,
) -> tuple[list[CutInOverlay], list[Diagnostic]]:
    """Validate A1 cut-ins against a compiled reel and type them as overlays.

    Returns ``(overlays, diagnostics)``. Never raises into the render loop — a
    malformed or unanchored cut-in is a diagnostic, not an exception.
    """

    spans = _source_spans(reel)
    overlays: list[CutInOverlay] = []
    diagnostics: list[Diagnostic] = []

    for cut_in in cut_ins:
        try:
            spec = _coerce_spec(cut_in)
        except ValidationError as exc:
            diagnostics.append(_invalid(f"cut-in failed validation: {exc.error_count()} error(s)",
                                        cut_in))
            continue

        if not _overlaps_any_span(spec, spans):
            diagnostics.append(_invalid(
                f"cut-in [{spec.at_s}, {spec.until_s}) overlaps no source segment; "
                f"it would be silently dropped at render",
                cut_in,
            ))
            continue

        overlays.append(CutInOverlay.model_validate(spec.model_dump()))

    return overlays, diagnostics
