"""B9a → A1 cut-ins map to validated CutInOverlay, or are typed-rejected.

SCOPE: the PURE mapper only. No render. Touches zero lines of footage_stitch.py.
The overlay RENDER stage (B9b) is deferred to a follow-up build.

D7 (corrected): CutInOverlay's time base is ABSOLUTE SOURCE TIME, not
segment-relative — overlays._relative_window subtracts segment_start_s, and you
only subtract an origin from an absolute time. So this mapper is validation +
typing, NOT arithmetic; the time fields pass through unchanged.

The real gap being closed: a cut-in outside EVERY segment is silently dropped
today (overlays.build_overlay_filtergraph filters on
``_relative_window(...) is not None``). A boundary-spanning cut-in is NOT a
rejection case — the library clamps it to each overlapping segment by design.
"""

from __future__ import annotations

from reel_af.dsl.cutins import map_cut_ins
from reel_af.dsl.models import CutInSpec, FootageReel, SourceSegment, Transition
from reel_af.render.overlays import CutInOverlay

SRC = "https://www.youtube.com/watch?v=abc123"


def _reel() -> FootageReel:
    """Two source segments: [10,20) and [30,40) in absolute source time."""
    return FootageReel(
        source_url=SRC,
        segments=[
            SourceSegment(segment_id="s0", source_url=SRC, start_s=10.0, end_s=20.0, text="a"),
            SourceSegment(segment_id="s1", source_url=SRC, start_s=30.0, end_s=40.0, text="b"),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="dissolve", duration_s=0.5)],
        duration_s=19.5,
    )


ZOOM = CutInSpec(type="zoom", at_s=12.0, until_s=14.0, line="hook", zoom_focus="upper")
VISUAL = CutInSpec(
    type="visual", at_s=31.0, until_s=33.0, line="payoff", image_prompt="a photo, no text"
)


def test_a1_cutins_map_to_validated_cutin_overlay():
    mapped, diags = map_cut_ins([ZOOM, VISUAL], reel=_reel())

    assert diags == []
    assert [o.type for o in mapped] == ["zoom", "visual"]
    assert all(isinstance(o, CutInOverlay) for o in mapped)
    assert mapped[0].zoom_focus == "upper"
    assert mapped[1].image_prompt == "a photo, no text"


def test_time_fields_pass_through_unchanged():
    """D7: both sides are absolute source time — the mapping is identity here."""
    mapped, _ = map_cut_ins([ZOOM], reel=_reel())

    assert (mapped[0].at_s, mapped[0].until_s) == (ZOOM.at_s, ZOOM.until_s)


def test_cutin_outside_every_segment_is_typed_rejected_not_dropped():
    """The real gap: today overlays filters these out silently."""
    outside = CutInSpec(type="zoom", at_s=500.0, until_s=502.0, line="nope")

    mapped, diags = map_cut_ins([outside], reel=_reel())

    assert mapped == []
    assert "CUTIN_INVALID" in {d.code for d in diags}
    assert diags[0].severity == "error"


def test_cutin_in_the_gap_between_segments_is_rejected():
    """[20,30) is not covered by any segment — the reel never plays it."""
    in_gap = CutInSpec(type="zoom", at_s=22.0, until_s=24.0, line="gap")

    mapped, diags = map_cut_ins([in_gap], reel=_reel())

    assert mapped == []
    assert "CUTIN_INVALID" in {d.code for d in diags}


def test_boundary_spanning_cutin_is_accepted_and_clamped_by_the_library():
    """NOT a rejection case (D7): _relative_window clamps to each overlap."""
    spanning = CutInSpec(type="zoom", at_s=19.0, until_s=31.0, line="spans")

    mapped, diags = map_cut_ins([spanning], reel=_reel())

    assert diags == []
    assert len(mapped) == 1
    assert (mapped[0].at_s, mapped[0].until_s) == (19.0, 31.0)  # unclamped; library clamps


def test_partial_overlap_at_segment_head_is_accepted():
    partial = CutInSpec(type="zoom", at_s=8.0, until_s=12.0, line="head overlap")

    mapped, diags = map_cut_ins([partial], reel=_reel())

    assert diags == []
    assert len(mapped) == 1


def test_valid_and_invalid_cutins_are_partitioned():
    outside = CutInSpec(type="zoom", at_s=500.0, until_s=502.0, line="nope")

    mapped, diags = map_cut_ins([ZOOM, outside, VISUAL], reel=_reel())

    assert [o.type for o in mapped] == ["zoom", "visual"]
    assert len(diags) == 1
    assert diags[0].code == "CUTIN_INVALID"


def test_empty_cutins_is_a_clean_noop():
    mapped, diags = map_cut_ins([], reel=_reel())

    assert mapped == []
    assert diags == []


def test_black_segments_do_not_anchor_cutins():
    """A cut-in over a black segment has no source footage to punch into."""
    from reel_af.dsl.models import BlackSegment

    reel = FootageReel(
        source_url=SRC,
        segments=[
            SourceSegment(segment_id="s0", source_url=SRC, start_s=10.0, end_s=20.0, text="a"),
            BlackSegment(duration_s=2.0),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="fade", duration_s=0.0)],
        duration_s=12.0,
    )
    outside = CutInSpec(type="zoom", at_s=100.0, until_s=101.0, line="nope")

    mapped, diags = map_cut_ins([outside], reel=reel)

    assert mapped == []
    assert "CUTIN_INVALID" in {d.code for d in diags}


def test_mapper_accepts_raw_dicts_from_hook_plan(fixture_path):
    """hook-plan.json cut_ins arrive as plain dicts."""
    import json

    plan = json.loads(fixture_path("a1_hook_plan.json").read_text(encoding="utf-8"))
    raw_cut_ins = plan["clips"][0]["cut_ins"]

    reel = FootageReel(
        source_url=SRC,
        segments=[
            SourceSegment(segment_id="s0", source_url=SRC, start_s=4.12, end_s=30.0, text="a")
        ],
        transitions=[],
        duration_s=25.88,
    )
    mapped, diags = map_cut_ins(raw_cut_ins, reel=reel)

    assert diags == []
    assert [o.type for o in mapped] == ["zoom", "visual"]


def test_invalid_cutin_payload_is_rejected_not_raised():
    """A malformed cut-in is a diagnostic, not an exception into the render loop."""
    mapped, diags = map_cut_ins([{"type": "visual", "at_s": 12.0, "until_s": 13.0}], reel=_reel())

    assert mapped == []
    assert "CUTIN_INVALID" in {d.code for d in diags}


def test_mapper_is_pure_and_does_not_mutate_input():
    cut_ins = [ZOOM]
    map_cut_ins(cut_ins, reel=_reel())

    assert cut_ins == [ZOOM]
    assert ZOOM.at_s == 12.0
