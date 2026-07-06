"""Composite Transcript DSL v2 — data models, constants, and validators.

All pydantic models use ``ConfigDict(extra="forbid")``. Persisted root models
carry ``schema_version="1"`` and ``dsl_version="2"``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from reel_af.dsl.ast import Marker, SourceLocus

# ── Constants ──────────────────────────────────────────────────────

MATCH_QUALITY_FLOOR: float = 0.85
SNAP_TOLERANCE_S: float = 1.0
JOIN_GAP_LIMIT_S: float = 600.0

MAX_WORDS: int = 200_000
MAX_SEGMENTS: int = 1_000
MAX_REEL_DURATION_S: float = 900.0
MAX_FILTER_GRAPH_CHARS: int = 250_000

CANVAS_WIDTH: int = 1080
CANVAS_HEIGHT: int = 1920
FPS: int = 30
AUDIO_SAMPLE_RATE: int = 48_000
FFPROBE_DURATION_EPSILON_S: float = 0.15
FFMPEG_TIMEOUT_S: float = 120.0
DOWNLOAD_TIMEOUT_S: float = 60.0

# ── XfadeEffect type ──────────────────────────────────────────────

XfadeEffect = Literal[
    "dissolve",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "hblur",
    "circleopen",
    "radial",
    "pixelize",
    "fadeblack",
    "fadewhite",
    "fade",
    "none",
]

FADE_TO_COLOR_EFFECTS: frozenset[str] = frozenset({"fade", "fadeblack", "fadewhite"})

# ── Words and Alignment ───────────────────────────────────────────


class DslWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    w: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    conf: float | None = Field(default=None, ge=0, le=1)


class FallbackSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)


class WordsSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    words: list[DslWord] = Field(default_factory=list, max_length=MAX_WORDS)
    segments: list[FallbackSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_sidecar(self) -> WordsSidecar:
        if not self.words and not self.segments:
            raise ValueError("WordsSidecar requires at least one of words or segments")

        for w in self.words:
            if w.start > w.end:
                raise ValueError(
                    f"word '{w.w}' has start ({w.start}) after end ({w.end})"
                )

        for i in range(1, len(self.words)):
            if self.words[i].start < self.words[i - 1].start:
                raise ValueError(
                    f"non-monotonic word order at index {i}: "
                    f"word '{self.words[i].w}' start ({self.words[i].start}) "
                    f"< previous start ({self.words[i - 1].start})"
                )

        for i in range(1, len(self.segments)):
            if self.segments[i].start_s < self.segments[i - 1].start_s:
                raise ValueError(
                    f"non-monotonic fallback segment order at index {i}: "
                    f"start_s ({self.segments[i].start_s}) "
                    f"< previous start_s ({self.segments[i - 1].start_s})"
                )

        return self


# ── Alignment result types ─────────────────────────────────────────


class AlignedSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["aligned"] = "aligned"
    start_s: float
    end_s: float
    quality: float = Field(ge=0, le=1)
    word_range: tuple[int, int] | None = None
    fallback_segment_range: tuple[int, int] | None = None
    method: Literal["exact", "fuzzy", "cue_fallback"]


class UnmatchedSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["unmatched"] = "unmatched"
    normalized_text: str
    best_quality: float = Field(ge=0, le=1)
    reason: Literal["below_floor", "empty_source", "empty_query"]
    source: SourceLocus | None = None


AlignResult = Annotated[AlignedSpan | UnmatchedSpan, Field(discriminator="kind")]


# ── Diagnostics ────────────────────────────────────────────────────

DiagnosticCode = Literal[
    "UNSUPPORTED_INSERT",
    "UNSUPPORTED_FIND",
    "UNMATCHED_SEGMENT",
    "JOIN_REFUSED",
    "UNRESOLVED_HOLE",
    "EMPTY_COMPOSITE",
    "NON_RENDERABLE_REEL",
    "INVALID_MARKER",
    "INVALID_WORDS",
    "INVALID_TRANSITION",
    "MISSING_SEGMENT_ASSET",
    "CUTIN_INVALID",
    "RELEVANT_NO_MATCH",
    "CANDIDATE_NOT_FOUND",
]


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: DiagnosticCode
    message: str
    severity: Literal["warning", "error"]
    source: SourceLocus | None = None
    context: dict[str, Any] = Field(default_factory=dict)


# ── Source and Footage Reel ────────────────────────────────────────


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str
    source_id: str | None = None


class SourceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["source"] = "source"
    segment_id: str
    source_url: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str


class BlackSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["black"] = "black"
    duration_s: float = Field(gt=0)


Segment = Annotated[SourceSegment | BlackSegment, Field(discriminator="kind")]


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_index: int = Field(ge=0)
    after_index: int = Field(ge=0)
    effect: XfadeEffect = "fade"
    duration_s: float = Field(ge=0)
    audio_fade: bool = True

    @model_validator(mode="after")
    def _validate_transition(self) -> Transition:
        if self.effect == "none" and self.duration_s != 0.0:
            raise ValueError(
                f"effect='none' requires duration_s=0, got {self.duration_s}"
            )
        return self


def _segment_duration(seg: SourceSegment | BlackSegment) -> float:
    if isinstance(seg, BlackSegment) or (hasattr(seg, "kind") and seg.kind == "black"):
        return seg.duration_s
    return seg.end_s - seg.start_s


class FootageReel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    dsl_version: Literal["2"] = "2"
    source_url: str
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS)
    transitions: list[Transition] = Field(default_factory=list)
    duration_s: float = Field(gt=0, le=MAX_REEL_DURATION_S)

    @model_validator(mode="after")
    def _validate_reel(self) -> FootageReel:
        n_segs = len(self.segments)
        n_trans = len(self.transitions)
        expected_trans = max(0, n_segs - 1)
        if n_trans != expected_trans:
            raise ValueError(
                f"transition count mismatch: expected {expected_trans} "
                f"(segments - 1), got {n_trans}"
            )

        for i, t in enumerate(self.transitions):
            if t.before_index != i or t.after_index != i + 1:
                raise ValueError(
                    f"transition {i} has non-adjacent indexes "
                    f"({t.before_index}, {t.after_index}), "
                    f"expected ({i}, {i + 1})"
                )

        for i, t in enumerate(self.transitions):
            if t.effect == "none":
                continue
            left_dur = _segment_duration(self.segments[t.before_index])
            right_dur = _segment_duration(self.segments[t.after_index])
            min_dur = min(left_dur, right_dur)
            if t.effect not in FADE_TO_COLOR_EFFECTS:
                if t.duration_s <= 0 or t.duration_s >= min_dur:
                    raise ValueError(
                        f"xfade transition {i} (effect={t.effect!r}) requires "
                        f"0 < duration_s < min(left={left_dur}, right={right_dur})={min_dur}, "
                        f"got duration_s={t.duration_s}"
                    )
            else:
                if t.duration_s > 0 and (left_dur < t.duration_s or right_dur < t.duration_s):
                    raise ValueError(
                        f"fade-to-color transition {i} (effect={t.effect!r}) requires "
                        f"each adjacent segment duration >= duration_s={t.duration_s}, "
                        f"got left={left_dur}, right={right_dur}"
                    )

        derived = self._derive_duration()
        if abs(self.duration_s - derived) > FFPROBE_DURATION_EPSILON_S:
            raise ValueError(
                f"duration_s={self.duration_s} does not match derived "
                f"duration={derived} within tolerance "
                f"{FFPROBE_DURATION_EPSILON_S}"
            )

        return self

    def _derive_duration(self) -> float:
        total = sum(_segment_duration(s) for s in self.segments)
        for t in self.transitions:
            if t.effect != "none" and t.effect not in FADE_TO_COLOR_EFFECTS:
                total -= t.duration_s
        return total


# ── Compile result ─────────────────────────────────────────────────


class CompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "warning", "error"]
    plan: FootageReel | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)


# ── Download / Asset types ─────────────────────────────────────────


class DownloadedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    path: Path
    source_start_s: float
    source_end_s: float


SegmentAssetMap = Mapping[str, DownloadedSegment]


class SegmentFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    source_url: str
    start_s: float
    end_s: float
    target_path: Path


# ── Resolver types ─────────────────────────────────────────────────


class HoleDomain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["primitive", "duration_s", "count", "selector", "file_stem", "edge"]
    candidates: tuple[str | float | int, ...] = ()
    min_value: float | int | None = None
    max_value: float | int | None = None
    excluded: tuple[str | float | int, ...] = ()


class HoleContext(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    marker: Marker
    field_name: str
    domain: HoleDomain
    source: SourceLocus | None = None
    before_text: str | None = None
    after_text: str | None = None


class HoleChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | float | int


class ResolveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    text: str
    changed: bool
    choices: list[HoleChoice] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


# ── Renderability validation ───────────────────────────────────────


class RenderabilityError(Exception):
    pass


def validate_renderable(reel: FootageReel | Any) -> None:
    """Raise ``RenderabilityError`` for any reel that cannot be rendered.

    This function checks invariants that do not fit cleanly in field validators.
    Production code must never use Python ``assert`` for validation.
    """
    segments = getattr(reel, "segments", None)
    if not segments:
        raise RenderabilityError("reel has no segments")

    transitions = getattr(reel, "transitions", None)
    if transitions is None:
        transitions = []

    n_segs = len(segments)
    expected_trans = max(0, n_segs - 1)
    if len(transitions) != expected_trans:
        raise RenderabilityError(
            f"transition count mismatch: expected {expected_trans}, "
            f"got {len(transitions)}"
        )

    duration_s = getattr(reel, "duration_s", 0)
    if duration_s <= 0:
        raise RenderabilityError(f"reel duration must be positive, got {duration_s}")

    if duration_s > MAX_REEL_DURATION_S:
        raise RenderabilityError(
            f"reel duration {duration_s} exceeds maximum {MAX_REEL_DURATION_S}"
        )


def _rebuild_forward_refs() -> None:
    from reel_af.dsl.ast import Marker as _Marker
    from reel_af.dsl.ast import SourceLocus as _SourceLocus

    _ns = {"Marker": _Marker, "SourceLocus": _SourceLocus, "Path": Path}
    UnmatchedSpan.model_rebuild(_types_namespace=_ns)
    Diagnostic.model_rebuild(_types_namespace=_ns)
    HoleContext.model_rebuild(_types_namespace=_ns)


__all__ = [
    "AlignedSpan",
    "AlignResult",
    "AUDIO_SAMPLE_RATE",
    "BlackSegment",
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "CompileResult",
    "Diagnostic",
    "DiagnosticCode",
    "DOWNLOAD_TIMEOUT_S",
    "DownloadedSegment",
    "DslWord",
    "FADE_TO_COLOR_EFFECTS",
    "FallbackSegment",
    "FFMPEG_TIMEOUT_S",
    "FFPROBE_DURATION_EPSILON_S",
    "FootageReel",
    "FPS",
    "HoleChoice",
    "HoleContext",
    "HoleDomain",
    "JOIN_GAP_LIMIT_S",
    "MATCH_QUALITY_FLOOR",
    "MAX_FILTER_GRAPH_CHARS",
    "MAX_REEL_DURATION_S",
    "MAX_SEGMENTS",
    "MAX_WORDS",
    "RenderabilityError",
    "ResolveResult",
    "Segment",
    "SegmentAssetMap",
    "SegmentFetchRequest",
    "SNAP_TOLERANCE_S",
    "SourceRef",
    "SourceSegment",
    "Transition",
    "UnmatchedSpan",
    "validate_renderable",
    "WordsSidecar",
    "XfadeEffect",
]
