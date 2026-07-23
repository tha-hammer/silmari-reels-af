"""AF-3it / B3 — additive ``AudioBed`` DSL model + renderability postconditions.

Backward-compatible by construction: ``FootageReel.audio_bed`` and
``beat_grid`` are optional, so every existing reel (and artifact dump) still
validates. ``validate_renderable`` gains two postconditions: the bed starts
within the reel, and the beat grid is finite + strictly increasing.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from reel_af.dsl.models import (
    AudioBed,
    FootageReel,
    RenderabilityError,
    SourceSegment,
    validate_renderable,
)


def _reel(**kw) -> FootageReel:
    return FootageReel(
        source_url="https://e/x.mp4",
        duration_s=3.0,
        segments=[
            SourceSegment(
                segment_id="s0", source_url="https://e/x.mp4",
                start_s=0.0, end_s=3.0, text="hi",
            )
        ],
        **kw,
    )


def _bed(**kw) -> AudioBed:
    payload = {"track_ref": "a1://beds/lofi.mp3", "gain_db": -12.0,
               "start_offset_s": 0.0, "ducking": True}
    payload.update(kw)
    return AudioBed(**payload)


# ── Model shape ──────────────────────────────────────────────────────


def test_audio_bed_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AudioBed(track_ref="a1://b.mp3", gain_db=0.0, start_offset_s=0.0,
                 ducking=False, loop=True)


def test_audio_bed_requires_track_ref():
    with pytest.raises(ValidationError):
        _bed(track_ref="")


def test_audio_bed_rejects_negative_offset():
    with pytest.raises(ValidationError):
        _bed(start_offset_s=-0.1)


# ── Backward compatibility ───────────────────────────────────────────


def test_reel_without_audio_bed_still_valid():
    validate_renderable(_reel())


def test_pre_audio_bed_dump_still_parses():
    """An artifact dumped before the fields existed must round-trip."""
    dump = _reel().model_dump()
    dump.pop("audio_bed", None)
    dump.pop("beat_grid", None)
    reel = FootageReel.model_validate(dump)
    assert reel.audio_bed is None and reel.beat_grid is None
    validate_renderable(reel)


# ── Renderability postconditions ─────────────────────────────────────


def test_bed_within_reel_is_renderable():
    validate_renderable(_reel(audio_bed=_bed(start_offset_s=1.5)))


def test_bed_starting_at_or_past_reel_end_rejected():
    with pytest.raises(RenderabilityError):
        validate_renderable(_reel(audio_bed=_bed(start_offset_s=3.0)))


def test_non_finite_bed_offset_rejected():
    bed = _bed().model_copy(update={"start_offset_s": math.inf})
    with pytest.raises(RenderabilityError):
        validate_renderable(_reel(audio_bed=bed))


def test_monotonic_beat_grid_is_renderable():
    validate_renderable(_reel(beat_grid=[0.0, 0.5, 1.0, 2.5]))


def test_non_monotonic_beat_grid_rejected():
    with pytest.raises(RenderabilityError):
        validate_renderable(_reel(beat_grid=[1.0, 0.5]))


def test_equal_adjacent_beats_rejected():
    with pytest.raises(RenderabilityError):
        validate_renderable(_reel(beat_grid=[0.5, 0.5, 1.0]))


def test_non_finite_beat_rejected():
    with pytest.raises(RenderabilityError):
        validate_renderable(_reel(beat_grid=[0.5, math.nan]))
