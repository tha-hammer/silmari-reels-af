"""Composite Transcript DSL v2 — compiler.

``compile_composite(doc, words, source) -> CompileResult``

Ordering:
1. Parse marker loci
2. Align source segments to word timings
3. Apply extend markers (edge adjustments)
4. Apply point inserts (black segments)
5. Apply joins
6. Recompute segment indexes
7. Apply local transitions
8. Apply global ``trans none all``
9. Validate model and renderability
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reel_af.dsl.aligner import (
    align,
    sentence_boundaries,
    snap_edge,
    word_boundaries,
)
from reel_af.dsl.ast import Extend, Find, Hole, Insert, Join, Trans
from reel_af.dsl.composite import CompositeDoc, CompositeSegment
from reel_af.dsl.models import (
    DSL_HOOKS_WORKFLOW,
    FADE_TO_COLOR_EFFECTS,
    JOIN_GAP_LIMIT_S,
    SNAP_TOLERANCE_S,
    BlackSegment,
    CompileContext,
    CompileResult,
    Diagnostic,
    FootageReel,
    RenderabilityError,
    SourceRef,
    SourceSegment,
    Transition,
    UnmatchedSpan,
    WordsSidecar,
    validate_renderable,
)
from reel_af.dsl.relevant import load_candidate, search_relevant

# Workflow-scoped unsupported verbs (B3/D3). Keyed by workflow — NEVER a global
# set: [insert relevant] / [insert file] / [find relevant] are supported features
# on the default workflow. The A1 DSL-hooks workflow sources footage from the one
# A1 video, so corpus-sourced inserts and candidate search are out of contract.
UNSUPPORTED_VERBS_BY_WORKFLOW: dict[str, frozenset[str]] = {
    DSL_HOOKS_WORKFLOW: frozenset({"insert_corpus", "find"}),
}

SOURCE_INTERVAL_EPSILON_S = 1e-6


def compile_composite(
    doc: CompositeDoc,
    words: WordsSidecar,
    source: SourceRef,
    *,
    relevant_dir: Path | None = None,
    context: CompileContext | None = None,
) -> CompileResult:
    diagnostics: list[Diagnostic] = []

    if not doc.segments:
        return _error_result("EMPTY_COMPOSITE", "composite document has no segments", diagnostics)

    invalid = _check_invalid_markers(doc, diagnostics)
    if invalid:
        return _error_result_from(diagnostics)

    unsupported = _check_unsupported(doc, diagnostics, context=context)
    if unsupported:
        return _error_result_from(diagnostics)

    unresolved = _check_unresolved(doc, diagnostics)
    if unresolved:
        return _error_result_from(diagnostics)

    aligned = _align_segments(doc, words, source, diagnostics)
    if aligned is None:
        return _error_result_from(diagnostics)

    # Verify raw aligner output BEFORE _apply_extends/_apply_joins — joins legitimately
    # collapse spans, so checking after them would false-positive.
    if _verify_injective_spans(aligned, diagnostics):
        return _error_result_from(diagnostics)

    _apply_extends(doc, aligned, words, diagnostics)

    # Tile segments contiguously in source time (end_s -> next start_s) so no source
    # moment - and no spoken phrase - plays twice at a seam (AF-e1x).
    if _normalize_source_intervals(aligned, source.source_url, diagnostics):
        return _error_result_from(diagnostics)

    segments_and_markers = _build_segment_list(
        doc, aligned, source, diagnostics, words, relevant_dir,
    )
    if segments_and_markers is None:
        return _error_result_from(diagnostics)

    build_result = segments_and_markers

    join_result = _apply_joins(
        doc,
        build_result.segments,
        build_result.boundary_map,
        build_result.original_segment_indexes,
        build_result.segment_output_indexes,
        build_result.trans_markers,
        diagnostics,
    )
    if join_result is None:
        return _error_result_from(diagnostics)

    segments = join_result.segments
    trans_markers = join_result.trans_markers

    if _verify_no_source_interval_overlap(segments, diagnostics):
        return _error_result_from(diagnostics)

    transitions = _build_transitions(segments, trans_markers, doc, diagnostics)
    if transitions is None:
        return _error_result_from(diagnostics)

    duration_s = _derive_duration(segments, transitions)

    try:
        reel = FootageReel(
            source_url=source.source_url,
            segments=segments,
            transitions=transitions,
            duration_s=duration_s,
        )
    except Exception as e:
        diagnostics.append(Diagnostic(
            code="NON_RENDERABLE_REEL",
            message=str(e),
            severity="error",
        ))
        return _error_result_from(diagnostics)

    try:
        validate_renderable(reel)
    except RenderabilityError as e:
        diagnostics.append(Diagnostic(
            code="NON_RENDERABLE_REEL",
            message=str(e),
            severity="error",
        ))
        return _error_result_from(diagnostics)

    status = "warning" if any(d.severity == "warning" for d in diagnostics) else "ok"
    return CompileResult(status=status, plan=reel, diagnostics=diagnostics)


def _error_result(code: str, message: str, diagnostics: list[Diagnostic]) -> CompileResult:
    diagnostics.append(Diagnostic(code=code, message=message, severity="error"))
    return CompileResult(status="error", plan=None, diagnostics=diagnostics)


def _error_result_from(diagnostics: list[Diagnostic]) -> CompileResult:
    return CompileResult(status="error", plan=None, diagnostics=diagnostics)


def _check_invalid_markers(doc: CompositeDoc, diagnostics: list[Diagnostic]) -> bool:
    """Emit INVALID_MARKER for every marker read_composite could not parse.

    Without this the malformed marker is silently dropped and the reel renders
    without the directive — no diagnostic, no warning (the defect B2 closes).
    Mirrors _check_unresolved's shape.
    """

    found = False
    for invalid in doc.invalid_markers:
        diagnostics.append(Diagnostic(
            code="INVALID_MARKER",
            message=f"invalid marker {invalid.text!r}: {invalid.message}",
            severity="error",
            source=invalid.source,
        ))
        found = True
    return found


def _unsupported_code_for(marker: Any, workflow: str) -> str | None:
    """Pure lookup: the diagnostic code for a marker on a workflow, or None.

    No side effects — the caller owns diagnostic emission (CodeCleanup: control
    expressions stay pure questions).
    """

    if workflow not in UNSUPPORTED_VERBS_BY_WORKFLOW:
        return None
    unsupported = UNSUPPORTED_VERBS_BY_WORKFLOW[workflow]
    if isinstance(marker, Find):
        return "UNSUPPORTED_FIND" if "find" in unsupported else None
    if isinstance(marker, Insert) and _is_corpus_insert(marker):
        return "UNSUPPORTED_INSERT" if "insert_corpus" in unsupported else None
    return None


def _is_corpus_insert(marker: Insert) -> bool:
    """True for [insert relevant ...] / [insert file ...] — corpus-sourced inserts.

    [insert black N] is a pure black segment and stays supported everywhere.
    """

    return marker.kind == "insert" and getattr(marker, "target", None) != "black"


def _check_unsupported(
    doc: CompositeDoc,
    diagnostics: list[Diagnostic],
    *,
    context: CompileContext | None = None,
) -> bool:
    """Workflow-scoped marker rejection.

    ``context is None`` -> ``False``: byte-for-byte the default-workflow behavior.
    UNSUPPORTED_INSERT / UNSUPPORTED_FIND are vestigial on the default workflow —
    ``[insert relevant]``, ``[insert file]`` and ``[find relevant]`` are supported,
    tested features (tests/dsl/test_compile_unsupported.py). Rejection is a
    per-workflow policy, never a global set.
    """

    if context is None:
        return False

    found = False
    for att in doc.markers:
        code = _unsupported_code_for(att.marker, context.workflow)
        if code is None:
            continue
        diagnostics.append(Diagnostic(
            code=code,
            message=f"marker unsupported on workflow {context.workflow}: {att.marker.kind}",
            severity="error",
            source=att.source,
        ))
        found = True
    return found


def _check_unresolved(doc: CompositeDoc, diagnostics: list[Diagnostic]) -> bool:
    found = False
    for att in doc.markers:
        marker = att.marker
        for field_name in _marker_hole_fields(marker):
            val = getattr(marker, field_name, None)
            if isinstance(val, Hole) and val.resolution is None:
                diagnostics.append(Diagnostic(
                    code="UNRESOLVED_HOLE",
                    message=f"unresolved hole in {marker.kind}.{field_name}",
                    severity="error",
                    source=att.source,
                ))
                found = True
    return found


def _marker_hole_fields(marker: Any) -> list[str]:
    kind = getattr(marker, "kind", "")
    if kind == "insert":
        return ["selector", "duration_s", "file_stem"]
    if kind == "find":
        return ["selector", "duration_s", "count"]
    if kind == "extend":
        return ["edge", "duration_s"]
    if kind == "trans":
        return ["primitive", "duration_s"]
    return []


class _AlignedSegment:
    __slots__ = ("seg", "start_s", "end_s", "text", "seg_id")

    def __init__(self, seg: CompositeSegment, start_s: float, end_s: float, text: str) -> None:
        self.seg = seg
        self.start_s = start_s
        self.end_s = end_s
        self.text = text
        self.seg_id = f"seg-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class _SourceInterval:
    source_url: str
    segment_id: str
    index: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class _SourceNeighborBounds:
    previous_end_s: float | None
    next_start_s: float | None


@dataclass(frozen=True)
class _BoundaryBinding:
    original_before_index: int
    left_output_index: int
    right_output_index: int


@dataclass(frozen=True)
class _SegmentBuildResult:
    segments: list[SourceSegment | BlackSegment]
    trans_markers: dict[int, Trans]
    boundary_map: dict[int, _BoundaryBinding]
    original_segment_indexes: list[int]
    segment_output_indexes: dict[int, int]


@dataclass(frozen=True)
class _JoinResult:
    segments: list[SourceSegment | BlackSegment]
    trans_markers: dict[int, Trans]


def _align_segments(
    doc: CompositeDoc,
    words: WordsSidecar,
    source: SourceRef,
    diagnostics: list[Diagnostic],
) -> list[_AlignedSegment] | None:
    aligned: list[_AlignedSegment] = []
    for seg in doc.segments:
        result = align(seg.normalized_text, words, source=seg.source, timecode_s=seg.timecode_s)
        if isinstance(result, UnmatchedSpan):
            diagnostics.append(Diagnostic(
                code="UNMATCHED_SEGMENT",
                message=f"segment {seg.index} could not be aligned: {result.reason} "
                        f"(quality={result.best_quality:.2f})",
                severity="error",
                source=seg.source,
            ))
            return None
        aligned.append(_AlignedSegment(seg, result.start_s, result.end_s, seg.normalized_text))
    return aligned


def _verify_injective_spans(
    aligned: list[_AlignedSegment], diagnostics: list[Diagnostic]
) -> bool:
    """Degenerate-plan guard: distinct composite segments must map to distinct
    source spans. A duplicate span means the aligner collapsed several segments
    onto one cue (the ``bd ate`` defect); never render it. Returns True (and
    emits a diagnostic) when the plan is degenerate.

    Runs on RAW aligner output, BEFORE ``_apply_extends``/``_apply_joins``:
    ``_apply_joins`` legitimately merges adjacent segments (intentional span
    reduction), so checking injectivity downstream of it would false-positive.
    """

    def _collapse(message: str, kind: str) -> bool:
        diagnostics.append(Diagnostic(
            code="SEGMENT_SPAN_COLLAPSE",
            message=message,
            severity="error",
            context={"kind": kind},
        ))
        return True

    spans = [(a.start_s, a.end_s) for a in aligned]
    if len(set(spans)) != len(spans):
        idxs = [a.seg.index for a in aligned]
        return _collapse(
            f"{len(spans)} segments collapsed to {len(set(spans))} distinct source "
            f"spans (segments {idxs}); alignment is non-injective",
            "injectivity",
        )
    return False


def _aligned_source_intervals(
    aligned: list[_AlignedSegment], source_url: str
) -> list[_SourceInterval]:
    return [
        _SourceInterval(
            source_url=source_url,
            segment_id=a.seg_id,
            index=a.seg.index,
            start_s=a.start_s,
            end_s=a.end_s,
        )
        for a in aligned
    ]


def _segment_source_intervals(
    segments: list[SourceSegment | BlackSegment],
) -> list[_SourceInterval]:
    intervals: list[_SourceInterval] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, SourceSegment):
            continue
        intervals.append(_SourceInterval(
            source_url=segment.source_url,
            segment_id=segment.segment_id,
            index=index,
            start_s=segment.start_s,
            end_s=segment.end_s,
        ))
    return intervals


def _source_interval_groups(
    intervals: list[_SourceInterval],
) -> dict[str, list[_SourceInterval]]:
    groups: dict[str, list[_SourceInterval]] = defaultdict(list)
    for interval in intervals:
        groups[interval.source_url].append(interval)
    return groups


def _first_source_interval_overlap(
    intervals: list[_SourceInterval],
) -> tuple[_SourceInterval, _SourceInterval, float] | None:
    for group in _source_interval_groups(intervals).values():
        ordered = sorted(group, key=lambda item: (item.start_s, item.end_s, item.segment_id))
        for left, right in zip(ordered, ordered[1:]):
            has_positive_overlap = left.end_s > right.start_s + SOURCE_INTERVAL_EPSILON_S
            if has_positive_overlap:
                overlap_s = max(0.0, left.end_s - right.start_s)
                return left, right, overlap_s
    return None


def _append_source_overlap_diagnostic(
    diagnostics: list[Diagnostic],
    left: _SourceInterval,
    right: _SourceInterval,
    overlap_s: float,
) -> None:
    diagnostics.append(Diagnostic(
        code="SOURCE_TIME_OVERLAP",
        message=(
            f"source interval overlap for {left.source_url}: "
            f"{left.segment_id} ends at {left.end_s:.6f}s, "
            f"{right.segment_id} starts at {right.start_s:.6f}s"
        ),
        severity="error",
        context={
            "source_url": left.source_url,
            "left_segment_id": left.segment_id,
            "right_segment_id": right.segment_id,
            "left_index": left.index,
            "right_index": right.index,
            "overlap_s": overlap_s,
        },
    ))


def _normalize_source_intervals(
    aligned: list[_AlignedSegment],
    source_url: str,
    diagnostics: list[Diagnostic],
) -> bool:
    """Clamp source cue overruns by source-time neighbors, preserving composite order."""
    ordered = sorted(aligned, key=lambda item: (item.start_s, item.end_s, item.seg.index))
    for cur, nxt in zip(ordered, ordered[1:]):
        overruns_next = cur.end_s > nxt.start_s + SOURCE_INTERVAL_EPSILON_S
        can_clamp_without_inversion = nxt.start_s > cur.start_s + SOURCE_INTERVAL_EPSILON_S
        if overruns_next and can_clamp_without_inversion:
            cur.end_s = nxt.start_s

    for interval in _aligned_source_intervals(aligned, source_url):
        if interval.end_s <= interval.start_s + SOURCE_INTERVAL_EPSILON_S:
            _append_source_overlap_diagnostic(diagnostics, interval, interval, 0.0)
            return True
    return False


def _verify_no_source_interval_overlap(
    segments: list[SourceSegment | BlackSegment],
    diagnostics: list[Diagnostic],
) -> bool:
    overlap = _first_source_interval_overlap(_segment_source_intervals(segments))
    if overlap is None:
        return False
    left, right, overlap_s = overlap
    _append_source_overlap_diagnostic(diagnostics, left, right, overlap_s)
    return True


def _source_neighbor_bounds(
    aligned: list[_AlignedSegment],
) -> dict[int, _SourceNeighborBounds]:
    ordered = sorted(aligned, key=lambda item: (item.start_s, item.end_s, item.seg.index))
    bounds: dict[int, _SourceNeighborBounds] = {}
    for idx, current in enumerate(ordered):
        previous_end_s = ordered[idx - 1].end_s if idx > 0 else None
        next_start_s = ordered[idx + 1].start_s if idx + 1 < len(ordered) else None
        bounds[current.seg.index] = _SourceNeighborBounds(
            previous_end_s=previous_end_s,
            next_start_s=next_start_s,
        )
    return bounds


def _apply_extends(
    doc: CompositeDoc,
    aligned: list[_AlignedSegment],
    words: WordsSidecar,
    diagnostics: list[Diagnostic],
) -> None:
    seg_by_index: dict[int, _AlignedSegment] = {a.seg.index: a for a in aligned}
    neighbor_bounds = _source_neighbor_bounds(aligned)

    for att in doc.markers:
        if not isinstance(att.marker, Extend):
            continue
        ext = att.marker
        seg_idx = att.segment_index
        if seg_idx is None:
            seg_idx = att.before_segment_index
        if seg_idx is None or seg_idx not in seg_by_index:
            continue

        a = seg_by_index[seg_idx]
        edge = ext.edge
        dur = ext.duration_s
        if isinstance(edge, Hole):
            edge = str(edge.resolution) if edge.resolution else "tail"
        if isinstance(dur, Hole):
            dur = float(dur.resolution) if dur.resolution else 0.0

        boundaries: list[float] = []
        if words.words:
            boundaries = sentence_boundaries(words.words) + word_boundaries(words.words)
        elif words.segments:
            for fs in words.segments:
                boundaries.extend([fs.start_s, fs.end_s])
        boundaries = sorted(set(boundaries))

        if edge == "tail":
            target = a.end_s + dur
            clamp_max = neighbor_bounds.get(a.seg.index, _SourceNeighborBounds(None, None)).next_start_s
            a.end_s = snap_edge(target, boundaries, tolerance=SNAP_TOLERANCE_S, clamp_max=clamp_max)
        elif edge == "head":
            target = a.start_s - dur
            clamp_min = neighbor_bounds.get(a.seg.index, _SourceNeighborBounds(None, None)).previous_end_s
            if clamp_min is None:
                clamp_min = 0.0
            a.start_s = snap_edge(target, boundaries, tolerance=SNAP_TOLERANCE_S, clamp_min=clamp_min)


def _build_segment_list(
    doc: CompositeDoc,
    aligned: list[_AlignedSegment],
    source: SourceRef,
    diagnostics: list[Diagnostic],
    words: WordsSidecar,
    relevant_dir: Path | None,
) -> _SegmentBuildResult | None:
    segments: list[SourceSegment | BlackSegment] = []
    trans_markers: dict[int, Trans] = {}

    boundary_markers = [
        att for att in doc.markers
        if att.locus == "boundary"
    ]

    insert_points: dict[int, list[Insert]] = {}
    trans_points: dict[int, Trans] = {}
    global_trans: Trans | None = None

    for att in boundary_markers:
        marker = att.marker
        before_idx = att.before_segment_index
        if before_idx is None:
            continue

        if isinstance(marker, Insert):
            insert_points.setdefault(before_idx, []).append(marker)
        elif isinstance(marker, Trans):
            if marker.all:
                global_trans = marker
            else:
                trans_points[before_idx] = marker

    for att in doc.markers:
        if att.locus == "segment_trailing" and isinstance(att.marker, Trans):
            if att.segment_index is not None:
                if att.marker.all:
                    global_trans = att.marker
                else:
                    trans_points[att.segment_index] = att.marker

    exclude_ranges = [(a.start_s, a.end_s) for a in aligned]

    seg_map: dict[int, int] = {}
    out_idx = 0

    for i, a in enumerate(aligned):
        seg_map[a.seg.index] = out_idx
        segments.append(SourceSegment(
            segment_id=a.seg_id,
            source_url=source.source_url,
            start_s=a.start_s,
            end_s=a.end_s,
            text=a.text,
        ))
        out_idx += 1

        if a.seg.index in insert_points:
            for ins in insert_points[a.seg.index]:
                result = _compile_insert(
                    ins, i, aligned, words, source, relevant_dir, doc,
                    exclude_ranges, diagnostics,
                )
                if result is None:
                    return None
                for seg in result:
                    segments.append(seg)
                    out_idx += 1

    original_segment_indexes = [a.seg.index for a in aligned]
    boundary_map = _rebuild_boundary_map(original_segment_indexes, seg_map)
    for binding in boundary_map.values():
        if binding.original_before_index in trans_points:
            trans_markers[binding.original_before_index] = trans_points[binding.original_before_index]
        elif global_trans is not None:
            trans_markers[binding.original_before_index] = global_trans

    return _SegmentBuildResult(
        segments=segments,
        trans_markers=trans_markers,
        boundary_map=boundary_map,
        original_segment_indexes=original_segment_indexes,
        segment_output_indexes=seg_map,
    )


def _compile_insert(
    ins: Insert,
    after_aligned_idx: int,
    aligned: list[_AlignedSegment],
    words: WordsSidecar,
    source: SourceRef,
    relevant_dir: Path | None,
    doc: CompositeDoc,
    exclude_ranges: list[tuple[float, float]],
    diagnostics: list[Diagnostic],
) -> list[SourceSegment | BlackSegment] | None:
    sel = ins.selector
    if isinstance(sel, Hole):
        sel = str(sel.resolution) if sel.resolution else "black"

    if sel == "black":
        dur = ins.duration_s
        if isinstance(dur, Hole):
            dur = float(dur.resolution) if dur.resolution else 1.0
        if dur is None:
            dur = 1.0
        return [BlackSegment(duration_s=dur)]

    if sel == "relevant":
        dur = ins.duration_s
        if isinstance(dur, Hole):
            dur = float(dur.resolution) if dur.resolution else 10.0
        if dur is None:
            dur = 10.0
        context = _gather_context(aligned, after_aligned_idx)
        ranges = search_relevant(words, context, dur, exclude_ranges)
        if not ranges:
            diagnostics.append(Diagnostic(
                code="RELEVANT_NO_MATCH",
                message=f"insert relevant: no matching content for ~{dur}s",
                severity="error",
                source=ins.source,
            ))
            return None
        result: list[SourceSegment | BlackSegment] = []
        for r in ranges:
            result.append(SourceSegment(
                segment_id=f"rel-{uuid.uuid4().hex[:8]}",
                source_url=source.source_url,
                start_s=r.start_s,
                end_s=r.end_s,
                text=r.text,
            ))
            exclude_ranges.append((r.start_s, r.end_s))
        return result

    if sel == "file":
        stem = ins.file_stem
        if isinstance(stem, Hole):
            stem = str(stem.resolution) if stem.resolution else None
        if stem is None:
            diagnostics.append(Diagnostic(
                code="CANDIDATE_NOT_FOUND",
                message="insert file: no file stem specified",
                severity="error",
                source=ins.source,
            ))
            return None
        rdir = relevant_dir or _derive_relevant_dir(doc)
        if rdir is None:
            diagnostics.append(Diagnostic(
                code="CANDIDATE_NOT_FOUND",
                message="insert file: no relevant directory available",
                severity="error",
                source=ins.source,
            ))
            return None
        candidate = load_candidate(rdir, stem)
        if candidate is None:
            diagnostics.append(Diagnostic(
                code="CANDIDATE_NOT_FOUND",
                message=f"insert file: candidate '{stem}' not found in {rdir}",
                severity="error",
                source=ins.source,
            ))
            return None
        result = []
        for r in candidate.ranges:
            result.append(SourceSegment(
                segment_id=f"cand-{uuid.uuid4().hex[:8]}",
                source_url=source.source_url,
                start_s=r.start_s,
                end_s=r.end_s,
                text=r.text,
            ))
        return result

    return []


def _gather_context(aligned: list[_AlignedSegment], after_idx: int) -> str:
    parts = []
    if 0 <= after_idx < len(aligned):
        parts.append(aligned[after_idx].text)
    if after_idx + 1 < len(aligned):
        parts.append(aligned[after_idx + 1].text)
    return " ".join(parts)


def _derive_relevant_dir(doc: CompositeDoc) -> Path | None:
    if doc.source_path is not None:
        return doc.source_path.parent / "relevant"
    return None


def _rebuild_boundary_map(
    original_segment_indexes: list[int],
    segment_output_indexes: dict[int, int],
) -> dict[int, _BoundaryBinding]:
    boundary_map: dict[int, _BoundaryBinding] = {}
    for left_orig, right_orig in zip(original_segment_indexes, original_segment_indexes[1:]):
        boundary_map[left_orig] = _BoundaryBinding(
            original_before_index=left_orig,
            left_output_index=segment_output_indexes[left_orig],
            right_output_index=segment_output_indexes[right_orig],
        )
    return boundary_map


def _remap_transition_markers(
    trans_markers: dict[int, Trans],
    boundary_map: dict[int, _BoundaryBinding],
    segment_count: int,
) -> dict[int, Trans]:
    remapped: dict[int, Trans] = {}
    for original_before_index, marker in trans_markers.items():
        binding = boundary_map.get(original_before_index)
        if binding is None:
            continue
        if binding.left_output_index == binding.right_output_index:
            continue
        before_index = binding.left_output_index
        if before_index < segment_count - 1:
            remapped[before_index] = marker
    return remapped


def _update_segment_output_indexes_after_merge(
    segment_output_indexes: dict[int, int],
    *,
    merge_idx: int,
    removed_idx: int,
) -> None:
    for original_index, output_index in list(segment_output_indexes.items()):
        if output_index == removed_idx:
            segment_output_indexes[original_index] = merge_idx
        elif output_index > removed_idx:
            segment_output_indexes[original_index] = output_index - 1


def _join_refused(
    diagnostics: list[Diagnostic],
    message: str,
    *,
    original_before_index: int,
    reason: str,
    context: dict[str, Any] | None = None,
) -> None:
    diagnostic_context = {
        "original_before_index": original_before_index,
        "reason": reason,
    }
    if context:
        diagnostic_context.update(context)
    diagnostics.append(Diagnostic(
        code="JOIN_REFUSED",
        message=message,
        severity="error",
        context=diagnostic_context,
    ))


def _apply_joins(
    doc: CompositeDoc,
    segments: list[SourceSegment | BlackSegment],
    boundary_map: dict[int, _BoundaryBinding],
    original_segment_indexes: list[int],
    segment_output_indexes: dict[int, int],
    trans_markers: dict[int, Trans],
    diagnostics: list[Diagnostic],
) -> _JoinResult | None:
    join_markers: list[tuple[int, Join]] = []
    for att in doc.markers:
        if isinstance(att.marker, Join):
            join_markers.append((att.before_segment_index or 0, att.marker))

    if not join_markers:
        return _JoinResult(
            segments=segments,
            trans_markers=_remap_transition_markers(trans_markers, boundary_map, len(segments)),
        )

    joined = list(segments)
    current_boundary_map = dict(boundary_map)
    current_segment_output_indexes = dict(segment_output_indexes)

    for orig_before_idx, join in sorted(join_markers, key=lambda x: x[0], reverse=True):
        binding = current_boundary_map.get(orig_before_idx)
        if binding is None:
            continue

        merge_idx = binding.left_output_index
        right_idx = binding.right_output_index
        if merge_idx == right_idx:
            continue
        if right_idx != merge_idx + 1:
            _join_refused(
                diagnostics,
                "join refused: marked boundary is not adjacent after inserts",
                original_before_index=orig_before_idx,
                reason="non_adjacent_boundary",
                context={"left_index": merge_idx, "right_index": right_idx},
            )
            return None

        left = joined[merge_idx]
        right = joined[right_idx]
        if not isinstance(left, SourceSegment) or not isinstance(right, SourceSegment):
            _join_refused(
                diagnostics,
                "join refused: marked boundary is not a source/source pair",
                original_before_index=orig_before_idx,
                reason="non_source_pair",
                context={"left_index": merge_idx, "right_index": right_idx},
            )
            return None

        if not _should_merge(left, right, orig_before_idx, join, diagnostics):
            return None

        merged = SourceSegment(
            segment_id=left.segment_id,
            source_url=left.source_url,
            start_s=left.start_s,
            end_s=right.end_s,
            text=left.text + " " + right.text,
        )
        joined[merge_idx] = merged
        joined.pop(right_idx)
        _update_segment_output_indexes_after_merge(
            current_segment_output_indexes,
            merge_idx=merge_idx,
            removed_idx=right_idx,
        )
        current_boundary_map = _rebuild_boundary_map(
            original_segment_indexes,
            current_segment_output_indexes,
        )

    return _JoinResult(
        segments=joined,
        trans_markers=_remap_transition_markers(trans_markers, current_boundary_map, len(joined)),
    )


def _should_merge(
    left: SourceSegment,
    right: SourceSegment,
    orig_before_idx: int,
    join: Join,
    diagnostics: list[Diagnostic],
) -> bool:
    if left.source_url != right.source_url:
        _join_refused(
            diagnostics,
            "join refused: different sources",
            original_before_index=orig_before_idx,
            reason="different_sources",
            context={
                "left_source_url": left.source_url,
                "right_source_url": right.source_url,
            },
        )
        return False

    source_time_reversed = right.start_s + SOURCE_INTERVAL_EPSILON_S < left.end_s
    if source_time_reversed:
        _join_refused(
            diagnostics,
            "join refused: non-forward source-time order",
            original_before_index=orig_before_idx,
            reason="non_forward_source_time",
            context={
                "left_segment_id": left.segment_id,
                "right_segment_id": right.segment_id,
                "left_end_s": left.end_s,
                "right_start_s": right.start_s,
            },
        )
        return False

    source_gap_s = max(0.0, right.start_s - left.end_s)
    if source_gap_s > JOIN_GAP_LIMIT_S:
        if join.mode in ("confirmed", "force"):
            return True
        _join_refused(
            diagnostics,
            f"join refused: gap {source_gap_s:.1f}s exceeds limit {JOIN_GAP_LIMIT_S}s; use [join confirmed]",
            original_before_index=orig_before_idx,
            reason="gap_exceeds_limit",
            context={"source_gap_s": source_gap_s},
        )
        return False

    return True


def _build_transitions(
    segments: list[SourceSegment | BlackSegment],
    trans_markers: dict[int, Trans],
    doc: CompositeDoc,
    diagnostics: list[Diagnostic],
) -> list[Transition] | None:
    transitions: list[Transition] = []
    has_global_none = any(
        isinstance(att.marker, Trans) and att.marker.all and _resolve_effect(att.marker) == "none"
        for att in doc.markers
    )

    for i in range(len(segments) - 1):
        if i in trans_markers:
            t = trans_markers[i]
            effect = _resolve_effect(t)
            dur = _resolve_duration(t)
            audio_fade = t.audio != "cut"

            if effect == "none":
                dur = 0.0

            left_dur = _seg_duration(segments[i])
            right_dur = _seg_duration(segments[i + 1])

            if effect != "none" and dur > 0:
                if effect not in FADE_TO_COLOR_EFFECTS:
                    min_adj = min(left_dur, right_dur)
                    if dur >= min_adj:
                        diagnostics.append(Diagnostic(
                            code="INVALID_TRANSITION",
                            message=f"transition {i} duration {dur}s >= adjacent segment min {min_adj:.2f}s",
                            severity="error",
                        ))
                        return None

            transitions.append(Transition(
                before_index=i,
                after_index=i + 1,
                effect=effect,
                duration_s=dur,
                audio_fade=audio_fade,
            ))
        elif has_global_none:
            transitions.append(Transition(
                before_index=i,
                after_index=i + 1,
                effect="none",
                duration_s=0.0,
                audio_fade=True,
            ))
        else:
            transitions.append(Transition(
                before_index=i,
                after_index=i + 1,
                effect="none",
                duration_s=0.0,
                audio_fade=True,
            ))

    return transitions


def _resolve_effect(t: Trans) -> str:
    prim = t.primitive
    if isinstance(prim, Hole):
        return str(prim.resolution) if prim.resolution else "none"
    return prim


def _resolve_duration(t: Trans) -> float:
    dur = t.duration_s
    if isinstance(dur, Hole):
        return float(dur.resolution) if dur.resolution else 1.0
    return dur


def _seg_duration(seg: SourceSegment | BlackSegment) -> float:
    if isinstance(seg, BlackSegment):
        return seg.duration_s
    return seg.end_s - seg.start_s


def _derive_duration(
    segments: list[SourceSegment | BlackSegment],
    transitions: list[Transition],
) -> float:
    total = sum(_seg_duration(s) for s in segments)
    for t in transitions:
        if t.effect != "none" and t.effect not in FADE_TO_COLOR_EFFECTS:
            total -= t.duration_s
    return total


def load_words(path: Any) -> WordsSidecar:
    import json
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return WordsSidecar.model_validate(data)


__all__ = [
    "compile_composite",
    "load_words",
]
