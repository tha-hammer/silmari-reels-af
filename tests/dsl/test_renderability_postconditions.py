"""B6 → validate_renderable enforces postconditions STRONGER than the schema.

D4: today validate_renderable (models.py) checks only four things — segment
presence, transition count, duration > 0, duration <= MAX. It does NOT check
finite spans, start_s < end_s, or allowed transition primitives. SourceSegment
(models.py) only bounds start_s >= 0 and end_s > 0 INDEPENDENTLY, so
``start_s == end_s`` and ``end_s = inf`` both construct cleanly today.

Strengthening is provably ADDITIVE: FootageReel._validate_reel is a pydantic
model_validator that runs at CONSTRUCTION, strictly earlier, so anything reaching
validate_renderable has already satisfied it. This can only add rejections.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from reel_af.dsl.models import (
    BlackSegment,
    RenderabilityError,
    SourceSegment,
    Transition,
    validate_renderable,
)

SRC = "https://www.youtube.com/watch?v=abc123"


class _Reel:
    """Duck-typed reel — validate_renderable reads via getattr (models.py).

    Built directly rather than through FootageReel so we can express reels the
    pydantic model_validator would reject at construction; validate_renderable is
    the post-construction gate under test.
    """

    def __init__(self, segments, transitions=None, duration_s=10.0):
        self.segments = segments
        self.transitions = transitions if transitions is not None else []
        self.duration_s = duration_s
        self.schema_version = "1"
        self.dsl_version = "2"


def _seg(start_s=0.0, end_s=5.0, segment_id="s0"):
    return SourceSegment(
        segment_id=segment_id, source_url=SRC, start_s=start_s, end_s=end_s, text="t"
    )


def test_baseline_reel_is_renderable():
    validate_renderable(_Reel([_seg()], [], duration_s=5.0))


def test_zero_length_span_is_not_renderable():
    """start_s == end_s constructs cleanly today (independent field bounds)."""
    assert _seg(start_s=5.0, end_s=5.0)  # proves the model itself allows it

    with pytest.raises(RenderabilityError, match="start_s"):
        validate_renderable(_Reel([_seg(start_s=5.0, end_s=5.0)], [], duration_s=5.0))


def test_inverted_span_is_not_renderable():
    with pytest.raises(RenderabilityError, match="start_s"):
        validate_renderable(_Reel([_seg(start_s=9.0, end_s=2.0)], [], duration_s=5.0))


def test_infinite_span_is_not_renderable():
    """float('inf') passes Field(gt=0) today (inf > 0 is True) — nothing checks finiteness."""
    assert _seg(start_s=0.0, end_s=float("inf"))  # proves the model itself allows it

    with pytest.raises(RenderabilityError, match="finite"):
        validate_renderable(_Reel([_seg(start_s=0.0, end_s=float("inf"))], [], duration_s=5.0))


def test_nan_span_is_rejected_at_construction_by_the_model():
    """nan fails Field(gt=0) (nan > 0 is False), so SourceSegment already blocks it.

    Recorded explicitly: the finiteness gap is inf-shaped, not nan-shaped, for
    typed segments. _require_finite still covers duck-typed/black spans below.
    """
    with pytest.raises(ValueError):
        _seg(start_s=0.0, end_s=float("nan"))


def test_nan_span_on_a_duck_typed_segment_is_not_renderable():
    class _NanSeg:
        kind = "source"
        start_s = 0.0
        end_s = float("nan")

    with pytest.raises(RenderabilityError, match="finite"):
        validate_renderable(_Reel([_NanSeg()], [], duration_s=5.0))


def test_non_finite_duration_is_not_renderable():
    with pytest.raises(RenderabilityError, match="finite"):
        validate_renderable(_Reel([_seg()], [], duration_s=float("inf")))


def test_disallowed_transition_primitive_is_not_renderable():
    class _BadTrans:
        effect = "notaprimitive"
        duration_s = 0.5

    with pytest.raises(RenderabilityError, match="transition"):
        validate_renderable(_Reel([_seg(), _seg(segment_id="s1")], [_BadTrans()], duration_s=9.0))


def test_allowed_transition_primitive_is_renderable():
    trans = Transition(before_index=0, after_index=1, effect="dissolve", duration_s=0.5)
    validate_renderable(_Reel([_seg(), _seg(segment_id="s1")], [trans], duration_s=9.5))


def test_black_segment_is_supported():
    validate_renderable(_Reel([BlackSegment(duration_s=2.5)], [], duration_s=2.5))


def test_non_finite_black_segment_is_not_renderable():
    class _BadBlack:
        kind = "black"
        duration_s = float("inf")

    with pytest.raises(RenderabilityError, match="finite"):
        validate_renderable(_Reel([_BadBlack()], [], duration_s=5.0))


def test_unknown_segment_kind_is_not_renderable():
    class _Weird:
        kind = "hologram"

    with pytest.raises(RenderabilityError, match="segment"):
        validate_renderable(_Reel([_Weird()], [], duration_s=5.0))


def test_schema_and_dsl_version_are_enforced():
    reel = _Reel([_seg()], [], duration_s=5.0)
    reel.dsl_version = "1"
    with pytest.raises(RenderabilityError, match="dsl_version"):
        validate_renderable(reel)


# ── Property: a rejected reel is never stitchable ──────────────────


@given(
    start_s=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    end_s=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_property_spans_must_strictly_increase(start_s, end_s):
    reel = _Reel([_seg(start_s=start_s, end_s=max(end_s, 0.001))], [], duration_s=5.0)
    span_ok = start_s < max(end_s, 0.001)

    try:
        validate_renderable(reel)
    except RenderabilityError:
        assert not span_ok or not math.isfinite(start_s)
        return
    assert span_ok
