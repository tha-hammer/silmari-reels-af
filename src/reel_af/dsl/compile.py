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
from pathlib import Path
from typing import Any

from reel_af.dsl.aligner import (
    align,
    sentence_boundaries,
    snap_edge,
    word_boundaries,
)
from reel_af.dsl.ast import Extend, Hole, Insert, Join, Trans
from reel_af.dsl.composite import CompositeDoc, CompositeSegment
from reel_af.dsl.models import (
    FADE_TO_COLOR_EFFECTS,
    JOIN_GAP_LIMIT_S,
    SNAP_TOLERANCE_S,
    BlackSegment,
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


def compile_composite(
    doc: CompositeDoc,
    words: WordsSidecar,
    source: SourceRef,
    *,
    relevant_dir: Path | None = None,
) -> CompileResult:
    diagnostics: list[Diagnostic] = []

    if not doc.segments:
        return _error_result("EMPTY_COMPOSITE", "composite document has no segments", diagnostics)

    unsupported = _check_unsupported(doc, diagnostics)
    if unsupported:
        return _error_result_from(diagnostics)

    unresolved = _check_unresolved(doc, diagnostics)
    if unresolved:
        return _error_result_from(diagnostics)

    aligned = _align_segments(doc, words, source, diagnostics)
    if aligned is None:
        return _error_result_from(diagnostics)

    _apply_extends(doc, aligned, words, diagnostics)

    segments_and_markers = _build_segment_list(
        doc, aligned, source, diagnostics, words, relevant_dir,
    )
    if segments_and_markers is None:
        return _error_result_from(diagnostics)

    segments, trans_markers = segments_and_markers

    segments = _apply_joins(doc, segments, diagnostics)
    if segments is None:
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


def _check_unsupported(doc: CompositeDoc, diagnostics: list[Diagnostic]) -> bool:
    return False


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


def _align_segments(
    doc: CompositeDoc,
    words: WordsSidecar,
    source: SourceRef,
    diagnostics: list[Diagnostic],
) -> list[_AlignedSegment] | None:
    aligned: list[_AlignedSegment] = []
    for seg in doc.segments:
        result = align(seg.normalized_text, words, source=seg.source)
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


def _apply_extends(
    doc: CompositeDoc,
    aligned: list[_AlignedSegment],
    words: WordsSidecar,
    diagnostics: list[Diagnostic],
) -> None:
    seg_by_index: dict[int, _AlignedSegment] = {a.seg.index: a for a in aligned}

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
            clamp_max = None
            idx_in_list = aligned.index(a)
            if idx_in_list + 1 < len(aligned):
                clamp_max = aligned[idx_in_list + 1].start_s
            a.end_s = snap_edge(target, boundaries, tolerance=SNAP_TOLERANCE_S, clamp_max=clamp_max)
        elif edge == "head":
            target = a.start_s - dur
            clamp_min = 0.0
            idx_in_list = aligned.index(a)
            if idx_in_list > 0:
                clamp_min = aligned[idx_in_list - 1].end_s
            a.start_s = snap_edge(target, boundaries, tolerance=SNAP_TOLERANCE_S, clamp_min=clamp_min)


def _build_segment_list(
    doc: CompositeDoc,
    aligned: list[_AlignedSegment],
    source: SourceRef,
    diagnostics: list[Diagnostic],
    words: WordsSidecar,
    relevant_dir: Path | None,
) -> tuple[list[SourceSegment | BlackSegment], dict[int, Trans]] | None:
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

    boundary_idx = 0
    for i in range(len(segments) - 1):
        if aligned and boundary_idx < len(aligned):
            orig_idx = None
            for a in aligned:
                if seg_map.get(a.seg.index) == i:
                    orig_idx = a.seg.index
                    break
            if orig_idx is not None and orig_idx in trans_points:
                trans_markers[i] = trans_points[orig_idx]
            elif global_trans is not None:
                trans_markers[i] = global_trans
        boundary_idx += 1

    return segments, trans_markers


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


def _apply_joins(
    doc: CompositeDoc,
    segments: list[SourceSegment | BlackSegment],
    diagnostics: list[Diagnostic],
) -> list[SourceSegment | BlackSegment] | None:
    join_markers: list[tuple[int, Join]] = []
    for att in doc.markers:
        if isinstance(att.marker, Join):
            join_markers.append((att.before_segment_index or 0, att.marker))

    if not join_markers:
        return segments

    joined = list(segments)

    for orig_before_idx, join in sorted(join_markers, key=lambda x: x[0], reverse=True):
        merge_idx = None
        for i, seg in enumerate(joined):
            if isinstance(seg, SourceSegment) and i + 1 < len(joined):
                next_seg = joined[i + 1]
                if isinstance(next_seg, SourceSegment) and seg.source_url == next_seg.source_url:
                    if _should_merge(seg, next_seg, orig_before_idx, join, diagnostics):
                        merge_idx = i
                        break

        if merge_idx is not None:
            left = joined[merge_idx]
            right = joined[merge_idx + 1]
            if isinstance(left, SourceSegment) and isinstance(right, SourceSegment):
                merged = SourceSegment(
                    segment_id=left.segment_id,
                    source_url=left.source_url,
                    start_s=min(left.start_s, right.start_s),
                    end_s=max(left.end_s, right.end_s),
                    text=left.text + " " + right.text,
                )
                joined[merge_idx] = merged
                joined.pop(merge_idx + 1)

    return joined


def _should_merge(
    left: SourceSegment,
    right: SourceSegment,
    orig_before_idx: int,
    join: Join,
    diagnostics: list[Diagnostic],
) -> bool:
    gap = right.start_s - left.end_s

    if left.source_url != right.source_url:
        if join.mode == "force":
            return True
        diagnostics.append(Diagnostic(
            code="JOIN_REFUSED",
            message="join refused: different sources; use [join force]",
            severity="error",
        ))
        return False

    if gap > JOIN_GAP_LIMIT_S:
        if join.mode in ("confirmed", "force"):
            return True
        diagnostics.append(Diagnostic(
            code="JOIN_REFUSED",
            message=f"join refused: gap {gap:.1f}s exceeds limit {JOIN_GAP_LIMIT_S}s; use [join confirmed]",
            severity="error",
        ))
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
