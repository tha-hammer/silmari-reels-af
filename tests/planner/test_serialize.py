from __future__ import annotations

from types import SimpleNamespace

import pytest

from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import CutInSpec, DslWord, WordsSidecar
from reel_af.dsl.parser import parse_marker, serialize_marker
from reel_af.planner.models import CutIn, CutInKind, Interrupt, InterruptKind, XfadeEffect
from reel_af.planner.serialize import (
    build_hook_plan,
    interrupt_to_marker_text,
    resolve_timecodes,
    serialize_composite,
)


def _words() -> WordsSidecar:
    return WordsSidecar(
        words=[
            DslWord(w="they", start=4.1, end=4.3),
            DslWord(w="pattern", start=4.3, end=4.7),
            DslWord(w="match", start=4.7, end=5.0),
            DslWord(w="at", start=6.0, end=6.1),
            DslWord(w="scale", start=6.1, end=6.5),
            DslWord(w="that", start=6.5, end=6.7),
            DslWord(w="feels", start=6.7, end=7.0),
            DslWord(w="like", start=7.0, end=7.2),
            DslWord(w="reasoning", start=7.2, end=7.8),
        ]
    )


def _bp() -> SimpleNamespace:
    return SimpleNamespace(
        template_="hook_context_value_payoff_cta",
        target_duration_s=28.0,
        hook=SimpleNamespace(
            type="curiosity_gap",
            banner_line="They fake it.",
            span_quote="they pattern match",
        ),
        beats=[
            SimpleNamespace(
                role="hook",
                span_quote="they pattern match",
                max_len_s=3.0,
                interrupt_out=SimpleNamespace(kind="trans", effect="dissolve", dur_s=0.5),
                cutin=SimpleNamespace(
                    type="zoom",
                    at_s=4.2,
                    until_s=4.8,
                    line="they pattern match",
                    zoom_focus="upper",
                ),
            ),
            SimpleNamespace(
                role="value",
                span_quote="at scale that feels like reasoning",
                max_len_s=3.0,
                interrupt_out=SimpleNamespace(kind="join"),
                cutin=None,
            ),
        ],
        loop=SimpleNamespace(strategy="tie_final_to_hook", final_span_quote="they pattern match"),
        engagement_primary="send",
        cta=SimpleNamespace(hardness="soft", placements=["end"]),
    )


def test_verbatim_quote_resolves_above_floor():
    resolved = resolve_timecodes(
        [{"span_quote": "they pattern match", "max_len_s": 5.0}],
        _words(),
    )

    r = resolved[0]
    assert r.resolved
    assert r.quality == 1.0
    assert (r.start_s, r.end_s) == (4.1, 5.0)


def test_below_floor_quote_flagged():
    resolved = resolve_timecodes(
        [{"span_quote": "totally unrelated phrase", "max_len_s": 5.0}],
        _words(),
    )

    assert not resolved[0].resolved
    assert resolved[0].reason == "below_floor"


def test_trans_interrupt_round_trips():
    text = interrupt_to_marker_text({"kind": "trans", "effect": "dissolve", "dur_s": 0.5})
    marker = parse_marker(text)

    assert marker.kind == "trans"
    assert serialize_marker(marker) == "[trans dissolve 0.5]"


def test_baml_enum_interrupt_maps_to_dsl_wire_tokens():
    text = interrupt_to_marker_text(
        Interrupt(
            kind=InterruptKind.Trans,
            effect=XfadeEffect.Smoothleft,
            dur_s=0.25,
        )
    )

    assert serialize_marker(parse_marker(text)) == "[trans smoothleft 0.25]"


def test_join_and_black_interrupts_round_trip():
    assert parse_marker(interrupt_to_marker_text({"kind": "join"})).kind == "join"

    black = parse_marker(interrupt_to_marker_text({"kind": "black", "dur_s": 0.4}))
    assert black.kind == "insert"
    assert black.selector == "black"
    assert serialize_marker(black) == "[insert black 0.4]"


def test_writer_round_trips_segments_and_markers():
    bp = _bp()
    resolved = resolve_timecodes(bp.beats, _words())

    text = serialize_composite(bp, resolved)
    doc = read_composite(text)

    assert not doc.invalid_markers
    assert len(doc.segments) == len(bp.beats)
    assert doc.segments[0].timecode_s == resolved[0].start_s
    assert doc.segments[0].normalized_text == "they pattern match"
    assert sorted(marker.marker.kind for marker in doc.markers) == ["join", "trans"]


def test_hook_plan_matches_consumer_shape():
    bp = _bp()
    resolved = resolve_timecodes(bp.beats, _words())
    cut_ins = [
        {
            "type": "zoom",
            "at_s": 4.2,
            "until_s": 4.8,
            "line": "they pattern match",
            "zoom_focus": "upper",
        },
        {
            "type": "visual",
            "at_s": 6.2,
            "until_s": 7.0,
            "line": "scale",
            "image_prompt": "single subject, no text",
        },
    ]

    plan = build_hook_plan(
        source_url="https://www.youtube.com/watch?v=abc123",
        hook=bp.hook,
        span=resolved[0],
        cut_ins=cut_ins,
        composite_ref="/tmp/out/composite.ts.md",
    )

    assert plan["schema_version"] == "1"
    assert plan["workflow"] == "dsl_hooks"
    assert plan["source_id"] == "abc123"
    clip = plan["clips"][0]
    consumer_read_fields = {
        "idx",
        "start_s",
        "end_s",
        "excerpt",
        "composite_ref",
        "target",
        "idempotency_key",
        "cut_ins",
    }
    assert consumer_read_fields <= set(clip)
    assert clip["target"] == "reel-af.reel_dsl_hooks_to_reels"
    assert clip["composite_ref"] == "/tmp/out/composite.ts.md"
    assert clip["idx"] == 1
    for cut_in in clip["cut_ins"]:
        CutInSpec.model_validate(cut_in)


def test_hook_plan_accepts_relative_planner_cut_in_shape():
    bp = _bp()
    resolved = resolve_timecodes(bp.beats, _words())

    plan = build_hook_plan(
        source_url="https://www.youtube.com/watch?v=abc123",
        hook=bp.hook,
        span=resolved[0],
        cut_ins=[CutIn(type=CutInKind.Zoom, offset_s=0.2, dur_s=0.6, line="look")],
        composite_ref="/tmp/out/composite.ts.md",
    )

    cut_in = plan["clips"][0]["cut_ins"][0]
    assert cut_in["at_s"] == pytest.approx(resolved[0].start_s + 0.2)
    assert cut_in["until_s"] == pytest.approx(resolved[0].start_s + 0.8)
    CutInSpec.model_validate(cut_in)
