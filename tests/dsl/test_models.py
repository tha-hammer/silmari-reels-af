"""Phase 0 RED tests — DSL models, constants, and validators.

Tests import reel_af.dsl.models and validate:
- WordsSidecar rejects non-monotonic word timings and mutable default leakage
- FootageReel rejects bad transitions, bad source ranges, empty segments,
  wrong duration math, and non-adjacent transition indexes
- Production renderability errors are explicit exceptions, not AssertionError
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError


# ── Constants ──────────────────────────────────────────────────────

def test_constants_exist():
    from reel_af.dsl.models import (
        MATCH_QUALITY_FLOOR,
        SNAP_TOLERANCE_S,
        JOIN_GAP_LIMIT_S,
        MAX_WORDS,
        MAX_SEGMENTS,
        MAX_REEL_DURATION_S,
        MAX_FILTER_GRAPH_CHARS,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
        FPS,
        AUDIO_SAMPLE_RATE,
        FFPROBE_DURATION_EPSILON_S,
        FFMPEG_TIMEOUT_S,
        DOWNLOAD_TIMEOUT_S,
    )
    assert MATCH_QUALITY_FLOOR == 0.85
    assert SNAP_TOLERANCE_S == 1.0
    assert JOIN_GAP_LIMIT_S == 600.0
    assert MAX_WORDS == 200_000
    assert MAX_SEGMENTS == 1_000
    assert MAX_REEL_DURATION_S == 900.0
    assert MAX_FILTER_GRAPH_CHARS == 250_000
    assert CANVAS_WIDTH == 1080
    assert CANVAS_HEIGHT == 1920
    assert FPS == 30
    assert AUDIO_SAMPLE_RATE == 48_000
    assert FFPROBE_DURATION_EPSILON_S == 0.15
    assert FFMPEG_TIMEOUT_S == 120.0
    assert DOWNLOAD_TIMEOUT_S == 60.0


# ── DslWord ────────────────────────────────────────────────────────

def test_dsl_word_valid():
    from reel_af.dsl.models import DslWord
    w = DslWord(w="hello", start=1.0, end=1.5, conf=0.95)
    assert w.w == "hello"
    assert w.start == 1.0
    assert w.end == 1.5
    assert w.conf == 0.95


def test_dsl_word_rejects_empty_word():
    from reel_af.dsl.models import DslWord
    with pytest.raises(ValidationError):
        DslWord(w="", start=0.0, end=0.1)


def test_dsl_word_rejects_negative_start():
    from reel_af.dsl.models import DslWord
    with pytest.raises(ValidationError):
        DslWord(w="x", start=-1.0, end=0.1)


def test_dsl_word_conf_bounds():
    from reel_af.dsl.models import DslWord
    with pytest.raises(ValidationError):
        DslWord(w="x", start=0.0, end=0.1, conf=1.5)
    with pytest.raises(ValidationError):
        DslWord(w="x", start=0.0, end=0.1, conf=-0.1)


# ── WordsSidecar ───────────────────────────────────────────────────

def test_words_sidecar_valid_words():
    from reel_af.dsl.models import DslWord, WordsSidecar
    ws = WordsSidecar(words=[
        DslWord(w="hello", start=0.0, end=0.5),
        DslWord(w="world", start=0.5, end=1.0),
    ])
    assert len(ws.words) == 2


def test_words_sidecar_rejects_non_monotonic():
    from reel_af.dsl.models import DslWord, WordsSidecar
    with pytest.raises(ValidationError, match="[Mm]onotonic|order"):
        WordsSidecar(words=[
            DslWord(w="hello", start=1.0, end=1.5),
            DslWord(w="world", start=0.5, end=1.0),
        ])


def test_words_sidecar_rejects_start_after_end():
    from reel_af.dsl.models import DslWord, WordsSidecar
    with pytest.raises(ValidationError, match="start.*end|end.*start"):
        WordsSidecar(words=[
            DslWord(w="hello", start=1.5, end=1.0),
        ])


def test_words_sidecar_requires_words_or_segments():
    from reel_af.dsl.models import WordsSidecar
    with pytest.raises(ValidationError):
        WordsSidecar(words=[], segments=[])


def test_words_sidecar_mutable_default_isolation():
    from reel_af.dsl.models import DslWord, WordsSidecar
    ws1 = WordsSidecar(words=[DslWord(w="a", start=0.0, end=0.1)])
    ws2 = WordsSidecar(words=[DslWord(w="b", start=0.0, end=0.1)])
    assert ws1.words is not ws2.words


def test_words_sidecar_valid_fallback_segments():
    from reel_af.dsl.models import FallbackSegment, WordsSidecar
    ws = WordsSidecar(
        words=[],
        segments=[
            FallbackSegment(text="hello world", start_s=0.0, end_s=1.0),
            FallbackSegment(text="foo bar", start_s=1.0, end_s=2.0),
        ],
    )
    assert len(ws.segments) == 2


def test_words_sidecar_rejects_non_monotonic_fallback():
    from reel_af.dsl.models import FallbackSegment, WordsSidecar
    with pytest.raises(ValidationError, match="[Mm]onotonic|order"):
        WordsSidecar(
            words=[],
            segments=[
                FallbackSegment(text="hello", start_s=2.0, end_s=3.0),
                FallbackSegment(text="world", start_s=1.0, end_s=2.0),
            ],
        )


# ── FallbackSegment ────────────────────────────────────────────────

def test_fallback_segment_valid():
    from reel_af.dsl.models import FallbackSegment
    fs = FallbackSegment(text="hello", start_s=0.0, end_s=1.0)
    assert fs.text == "hello"


def test_fallback_segment_rejects_empty_text():
    from reel_af.dsl.models import FallbackSegment
    with pytest.raises(ValidationError):
        FallbackSegment(text="", start_s=0.0, end_s=1.0)


# ── SourceRef ──────────────────────────────────────────────────────

def test_source_ref():
    from reel_af.dsl.models import SourceRef
    sr = SourceRef(source_url="https://example.com/video.mp4")
    assert sr.source_url == "https://example.com/video.mp4"
    assert sr.source_id is None


# ── SourceSegment ──────────────────────────────────────────────────

def test_source_segment_valid():
    from reel_af.dsl.models import SourceSegment
    seg = SourceSegment(
        segment_id="seg-0001",
        source_url="https://example.com/video.mp4",
        start_s=10.0,
        end_s=20.0,
        text="They don't reason",
    )
    assert seg.kind == "source"
    assert seg.end_s - seg.start_s == 10.0


def test_source_segment_rejects_negative_start():
    from reel_af.dsl.models import SourceSegment
    with pytest.raises(ValidationError):
        SourceSegment(
            segment_id="seg-0001",
            source_url="https://example.com/video.mp4",
            start_s=-1.0,
            end_s=10.0,
            text="x",
        )


def test_source_segment_rejects_zero_end():
    from reel_af.dsl.models import SourceSegment
    with pytest.raises(ValidationError):
        SourceSegment(
            segment_id="seg-0001",
            source_url="https://example.com/video.mp4",
            start_s=0.0,
            end_s=0.0,
            text="x",
        )


# ── BlackSegment ──────────────────────────────────────────────────

def test_black_segment_valid():
    from reel_af.dsl.models import BlackSegment
    seg = BlackSegment(duration_s=2.5)
    assert seg.kind == "black"
    assert seg.duration_s == 2.5


def test_black_segment_rejects_zero():
    from reel_af.dsl.models import BlackSegment
    with pytest.raises(ValidationError):
        BlackSegment(duration_s=0.0)


def test_black_segment_rejects_negative():
    from reel_af.dsl.models import BlackSegment
    with pytest.raises(ValidationError):
        BlackSegment(duration_s=-1.0)


# ── Transition ─────────────────────────────────────────────────────

def test_transition_valid():
    from reel_af.dsl.models import Transition
    t = Transition(before_index=0, after_index=1, effect="fade", duration_s=0.5, audio_fade=True)
    assert t.before_index == 0
    assert t.after_index == 1


def test_transition_rejects_negative_index():
    from reel_af.dsl.models import Transition
    with pytest.raises(ValidationError):
        Transition(before_index=-1, after_index=0, effect="fade", duration_s=0.5)


def test_transition_none_requires_zero_duration():
    from reel_af.dsl.models import Transition
    with pytest.raises(ValidationError, match="none.*duration|duration.*none"):
        Transition(before_index=0, after_index=1, effect="none", duration_s=0.5)


def test_transition_none_with_zero_duration():
    from reel_af.dsl.models import Transition
    t = Transition(before_index=0, after_index=1, effect="none", duration_s=0.0)
    assert t.effect == "none"
    assert t.duration_s == 0.0


# ── FootageReel ────────────────────────────────────────────────────

def _make_source_segment(
    seg_id: str = "seg-0001",
    start_s: float = 0.0,
    end_s: float = 10.0,
    text: str = "hello",
) -> dict[str, Any]:
    return {
        "kind": "source",
        "segment_id": seg_id,
        "source_url": "https://example.com/video.mp4",
        "start_s": start_s,
        "end_s": end_s,
        "text": text,
    }


def _make_black_segment(duration_s: float = 2.5) -> dict[str, Any]:
    return {"kind": "black", "duration_s": duration_s}


def _make_transition(
    before_index: int = 0,
    after_index: int = 1,
    effect: str = "none",
    duration_s: float = 0.0,
    audio_fade: bool = True,
) -> dict[str, Any]:
    return {
        "before_index": before_index,
        "after_index": after_index,
        "effect": effect,
        "duration_s": duration_s,
        "audio_fade": audio_fade,
    }


def test_footage_reel_valid_two_segments():
    from reel_af.dsl.models import FootageReel
    reel = FootageReel(
        source_url="https://example.com/video.mp4",
        segments=[
            _make_source_segment("seg-0001", 0.0, 10.0),
            _make_source_segment("seg-0002", 15.0, 25.0),
        ],
        transitions=[_make_transition(0, 1, "none", 0.0)],
        duration_s=20.0,
    )
    assert len(reel.segments) == 2
    assert len(reel.transitions) == 1


def test_footage_reel_rejects_empty_segments():
    from reel_af.dsl.models import FootageReel
    with pytest.raises(ValidationError):
        FootageReel(
            source_url="https://example.com/video.mp4",
            segments=[],
            transitions=[],
            duration_s=10.0,
        )


def test_footage_reel_rejects_wrong_transition_count():
    from reel_af.dsl.models import FootageReel
    with pytest.raises(ValidationError, match="transition"):
        FootageReel(
            source_url="https://example.com/video.mp4",
            segments=[
                _make_source_segment("seg-0001", 0.0, 10.0),
                _make_source_segment("seg-0002", 15.0, 25.0),
            ],
            transitions=[],
            duration_s=20.0,
        )


def test_footage_reel_rejects_non_adjacent_transitions():
    from reel_af.dsl.models import FootageReel
    with pytest.raises(ValidationError, match="adjacent|ordered|index"):
        FootageReel(
            source_url="https://example.com/video.mp4",
            segments=[
                _make_source_segment("seg-0001", 0.0, 10.0),
                _make_source_segment("seg-0002", 15.0, 25.0),
                _make_source_segment("seg-0003", 30.0, 40.0),
            ],
            transitions=[
                _make_transition(0, 1, "none", 0.0),
                _make_transition(0, 2, "none", 0.0),
            ],
            duration_s=30.0,
        )


def test_footage_reel_rejects_duration_exceeding_max():
    from reel_af.dsl.models import FootageReel, MAX_REEL_DURATION_S
    with pytest.raises(ValidationError):
        FootageReel(
            source_url="https://example.com/video.mp4",
            segments=[_make_source_segment("seg-0001", 0.0, 1000.0)],
            transitions=[],
            duration_s=1000.0,
        )


def test_footage_reel_with_black_segment():
    from reel_af.dsl.models import FootageReel
    reel = FootageReel(
        source_url="https://example.com/video.mp4",
        segments=[
            _make_source_segment("seg-0001", 0.0, 10.0),
            _make_black_segment(2.5),
            _make_source_segment("seg-0002", 15.0, 25.0),
        ],
        transitions=[
            _make_transition(0, 1, "none", 0.0),
            _make_transition(1, 2, "none", 0.0),
        ],
        duration_s=22.5,
    )
    assert len(reel.segments) == 3


def test_footage_reel_xfade_duration_validation():
    """xfade primitives require 0 < duration_s < min(left_duration, right_duration)."""
    from reel_af.dsl.models import FootageReel
    with pytest.raises(ValidationError, match="duration|xfade|overlap"):
        FootageReel(
            source_url="https://example.com/video.mp4",
            segments=[
                _make_source_segment("seg-0001", 0.0, 5.0),
                _make_source_segment("seg-0002", 10.0, 15.0),
            ],
            transitions=[_make_transition(0, 1, "dissolve", 10.0)],
            duration_s=5.0,
        )


def test_footage_reel_duration_math():
    """duration_s must equal derived duration within FFPROBE_DURATION_EPSILON_S."""
    from reel_af.dsl.models import FootageReel
    with pytest.raises(ValidationError, match="duration"):
        FootageReel(
            source_url="https://example.com/video.mp4",
            segments=[
                _make_source_segment("seg-0001", 0.0, 10.0),
                _make_source_segment("seg-0002", 15.0, 25.0),
            ],
            transitions=[_make_transition(0, 1, "none", 0.0)],
            duration_s=99.0,
        )


# ── validate_renderable ───────────────────────────────────────────

def test_validate_renderable_passes():
    from reel_af.dsl.models import FootageReel, validate_renderable
    reel = FootageReel(
        source_url="https://example.com/video.mp4",
        segments=[
            _make_source_segment("seg-0001", 0.0, 10.0),
            _make_source_segment("seg-0002", 15.0, 25.0),
        ],
        transitions=[_make_transition(0, 1, "none", 0.0)],
        duration_s=20.0,
    )
    validate_renderable(reel)


def test_validate_renderable_raises_renderability_error_not_assert():
    """Production validation must not use Python assert — must be explicit exception."""
    from reel_af.dsl.models import RenderabilityError, validate_renderable

    class FakeReel:
        segments = []
        transitions = []
        duration_s = 0

    with pytest.raises(RenderabilityError):
        validate_renderable(FakeReel())

    with pytest.raises(Exception) as exc_info:
        validate_renderable(FakeReel())
    assert not isinstance(exc_info.value, AssertionError)


# ── Extra fields are forbidden ─────────────────────────────────────

def test_extra_fields_forbidden():
    from reel_af.dsl.models import DslWord, SourceSegment, BlackSegment
    with pytest.raises(ValidationError):
        DslWord(w="hello", start=0.0, end=0.5, bogus=True)
    with pytest.raises(ValidationError):
        SourceSegment(
            segment_id="x", source_url="x", start_s=0.0, end_s=1.0,
            text="x", bogus=True,
        )
    with pytest.raises(ValidationError):
        BlackSegment(duration_s=1.0, bogus=True)


# ── Schema / DSL version fields ───────────────────────────────────

def test_footage_reel_schema_version():
    from reel_af.dsl.models import FootageReel
    reel = FootageReel(
        source_url="https://example.com/video.mp4",
        segments=[_make_source_segment("seg-0001", 0.0, 10.0)],
        transitions=[],
        duration_s=10.0,
    )
    assert reel.schema_version == "1"
    assert reel.dsl_version == "2"


def test_words_sidecar_schema_version():
    from reel_af.dsl.models import DslWord, WordsSidecar
    ws = WordsSidecar(words=[DslWord(w="a", start=0.0, end=0.1)])
    assert ws.schema_version == "1"


# ── Type-narrowed fields ─────────────────────────────────────────


def test_downloaded_segment_path_accepts_path():
    from pathlib import Path
    from reel_af.dsl.models import DownloadedSegment
    ds = DownloadedSegment(
        segment_id="seg-001",
        path=Path("/tmp/clip.mp4"),
        source_start_s=0.0,
        source_end_s=10.0,
    )
    assert isinstance(ds.path, Path)


def test_downloaded_segment_path_coerces_str_to_path():
    from pathlib import Path
    from reel_af.dsl.models import DownloadedSegment
    ds = DownloadedSegment(
        segment_id="seg-001",
        path="/tmp/clip.mp4",
        source_start_s=0.0,
        source_end_s=10.0,
    )
    assert isinstance(ds.path, Path)


def test_segment_fetch_request_target_path_accepts_path():
    from pathlib import Path
    from reel_af.dsl.models import SegmentFetchRequest
    req = SegmentFetchRequest(
        segment_id="seg-001",
        source_url="https://example.com/video.mp4",
        start_s=0.0,
        end_s=10.0,
        target_path=Path("/tmp/out.mp4"),
    )
    assert isinstance(req.target_path, Path)


def test_unmatched_span_source_accepts_source_locus():
    from reel_af.dsl.ast import SourceLocus
    from reel_af.dsl.models import UnmatchedSpan
    locus = SourceLocus(line=1, col=1, raw="[find ?]")
    span = UnmatchedSpan(
        normalized_text="hello",
        best_quality=0.5,
        reason="below_floor",
        source=locus,
    )
    assert isinstance(span.source, SourceLocus)


def test_diagnostic_source_accepts_source_locus():
    from reel_af.dsl.ast import SourceLocus
    from reel_af.dsl.models import Diagnostic
    locus = SourceLocus(line=5, col=1, raw="[insert relevant]")
    diag = Diagnostic(
        code="UNSUPPORTED_INSERT",
        message="test",
        severity="error",
        source=locus,
    )
    assert isinstance(diag.source, SourceLocus)


def test_hole_context_marker_accepts_marker():
    from reel_af.dsl.ast import Insert, SourceLocus
    from reel_af.dsl.models import HoleContext, HoleDomain
    marker = Insert(selector="relevant", duration_s=5.0)
    domain = HoleDomain(name="duration_s")
    ctx = HoleContext(
        marker=marker,
        field_name="duration_s",
        domain=domain,
    )
    assert isinstance(ctx.marker, Insert)


def test_hole_context_source_accepts_source_locus():
    from reel_af.dsl.ast import Insert, SourceLocus
    from reel_af.dsl.models import HoleContext, HoleDomain
    locus = SourceLocus(line=3, col=1, raw="[insert relevant ?]")
    marker = Insert(selector="relevant", duration_s=5.0)
    domain = HoleDomain(name="duration_s")
    ctx = HoleContext(
        marker=marker,
        field_name="duration_s",
        domain=domain,
        source=locus,
    )
    assert isinstance(ctx.source, SourceLocus)
