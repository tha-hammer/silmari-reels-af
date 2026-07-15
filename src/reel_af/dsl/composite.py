"""Composite Transcript DSL v2 — .ts.md reader."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from reel_af.dsl.ast import Marker, SourceLocus
from reel_af.dsl.parser import MarkerError, parse_marker

_TIMECODE_RE = re.compile(r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+(.+)$")
_MARKER_INLINE_RE = re.compile(r"\s*(\[[^\]]+\])\s*$")
_MARKER_LINE_RE = re.compile(r"^\s*(\[[^\]]+\])\s*$")

LocusKind = Literal["segment_trailing", "boundary", "point"]


class MarkerAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker: Marker
    locus: LocusKind
    segment_index: int | None = None
    before_segment_index: int | None = None
    after_segment_index: int | None = None
    source: SourceLocus


class CompositeSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    timecode_s: float
    raw_text: str
    normalized_text: str
    source: SourceLocus
    trailing_markers: list[MarkerAttachment] = Field(default_factory=list)


class InvalidMarker(BaseModel):
    """A marker that failed to parse, preserved so the compiler can diagnose it.

    Without this the MarkerError is swallowed and the malformed marker is silently
    dropped — the reel then renders without the directive and nothing reports it.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    message: str
    source: SourceLocus


class CompositeDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    dsl_version: Literal["2"] = "2"
    source_path: Path | None = None
    segments: list[CompositeSegment] = Field(default_factory=list)
    markers: list[MarkerAttachment] = Field(default_factory=list)
    invalid_markers: list[InvalidMarker] = Field(default_factory=list)


def _parse_timecode(tc: str) -> float:
    parts = tc.split(":")
    h, m = int(parts[0]), int(parts[1])
    s_ms = parts[2].split(".")
    s, ms = int(s_ms[0]), int(s_ms[1])
    return h * 3600 + m * 60 + s + ms / 1000.0


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _record_invalid_marker(
    invalid_markers: list[InvalidMarker],
    marker_text: str,
    error: MarkerError,
    src: SourceLocus,
) -> None:
    """Preserve a marker that failed to parse, so the compiler can diagnose it.

    Shared by both swallow sites (inline + standalone) — one helper, not two copies.
    """

    invalid_markers.append(
        InvalidMarker(text=marker_text, message=str(error), source=src)
    )


def read_composite(text: str, *, source_path: Path | None = None) -> CompositeDoc:
    segments: list[CompositeSegment] = []
    markers: list[MarkerAttachment] = []
    invalid_markers: list[InvalidMarker] = []
    pending: list[tuple[SourceLocus, Marker, int]] = []
    seg_index = 0
    trailing_ok = False

    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            trailing_ok = False
            continue

        tc_match = _TIMECODE_RE.match(stripped)
        if tc_match:
            timecode_s = _parse_timecode(tc_match.group(1))
            raw_text = tc_match.group(2)

            inline_marker_match = _MARKER_INLINE_RE.search(raw_text)
            trailing_markers: list[MarkerAttachment] = []
            if inline_marker_match:
                marker_text = inline_marker_match.group(1)
                raw_text = raw_text[:inline_marker_match.start()].strip()
                src = SourceLocus(path=source_path, line=line_no, col=inline_marker_match.start() + 1, raw=marker_text)
                try:
                    parsed = parse_marker(marker_text, source=src)
                    att = MarkerAttachment(
                        marker=parsed,
                        locus="segment_trailing",
                        segment_index=seg_index,
                        source=src,
                    )
                    trailing_markers.append(att)
                    markers.append(att)
                except MarkerError as exc:
                    _record_invalid_marker(invalid_markers, marker_text, exc, src)

            seg_locus = SourceLocus(path=source_path, line=line_no, col=1, raw=stripped)
            seg = CompositeSegment(
                index=seg_index,
                timecode_s=timecode_s,
                raw_text=raw_text,
                normalized_text=_normalize_text(raw_text),
                source=seg_locus,
                trailing_markers=trailing_markers,
            )
            segments.append(seg)
            seg_index += 1
            trailing_ok = True
            continue

        marker_match = _MARKER_LINE_RE.match(stripped)
        if marker_match:
            marker_text = marker_match.group(1)
            src = SourceLocus(path=source_path, line=line_no, col=1, raw=stripped)
            try:
                parsed = parse_marker(marker_text, source=src)
            except MarkerError as exc:
                _record_invalid_marker(invalid_markers, marker_text, exc, src)
                trailing_ok = False
                continue

            if trailing_ok and segments:
                att = MarkerAttachment(
                    marker=parsed,
                    locus="segment_trailing",
                    segment_index=segments[-1].index,
                    source=src,
                )
                segments[-1].trailing_markers.append(att)
                markers.append(att)
            else:
                pending.append((src, parsed, line_no))
            continue

        trailing_ok = False

    for src, parsed, marker_line in pending:
        prev_idx = _prev_segment(marker_line, segments)
        next_idx = _next_segment(marker_line, segments)
        if prev_idx is not None and next_idx is not None:
            att = MarkerAttachment(
                marker=parsed,
                locus="boundary",
                before_segment_index=prev_idx,
                after_segment_index=next_idx,
                source=src,
            )
        elif next_idx is not None:
            att = MarkerAttachment(
                marker=parsed,
                locus="point",
                after_segment_index=next_idx,
                source=src,
            )
        elif prev_idx is not None:
            att = MarkerAttachment(
                marker=parsed,
                locus="point",
                before_segment_index=prev_idx,
                source=src,
            )
        else:
            att = MarkerAttachment(
                marker=parsed,
                locus="point",
                source=src,
            )
        markers.append(att)

    markers.sort(key=lambda m: m.source.line)

    return CompositeDoc(
        source_path=source_path,
        segments=segments,
        markers=markers,
        invalid_markers=invalid_markers,
    )


def _prev_segment(line_no: int, segments: list[CompositeSegment]) -> int | None:
    prev: int | None = None
    for seg in segments:
        if seg.source.line < line_no:
            prev = seg.index
    return prev


def _next_segment(line_no: int, segments: list[CompositeSegment]) -> int | None:
    for seg in segments:
        if seg.source.line > line_no:
            return seg.index
    return None


def read_composite_file(path: Path) -> CompositeDoc:
    text = path.read_text(encoding="utf-8")
    return read_composite(text, source_path=path)


__all__ = [
    "CompositeDoc",
    "CompositeSegment",
    "LocusKind",
    "MarkerAttachment",
    "read_composite",
    "read_composite_file",
]
