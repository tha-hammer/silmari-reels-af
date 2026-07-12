"""Reasoner-side tunable override validation (plan Behavior 1).

``safe_overrides(raw)`` is the reasoner's defensive gate: unknown keys are
dropped, known keys are coerced to their declared type and clamped to the
``tunables.json`` bounds, and anything uncoercible falls back to the preset
(is dropped). The output is always a subset of the tunable keys with every
value in range — safe to merge straight onto a loaded preset.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from reel_af.render.tunables import load_tunables, safe_overrides, tunable_keys

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def test_tunables_json_declares_every_planned_key():
    keys = set(tunable_keys())
    assert keys == {
        "reel_seconds", "overlay_accent", "overlay_vertical_anchor",
        "phrase_max_words", "phrase_max_dur_s", "phrase_gap_s", "phrase_hold_s",
        "phrase_uppercase", "font_scale", "card_opacity", "box_opacity",
        "accent_bar_px", "corner_radius", "anim_style", "anim_damping",
        "anim_mass", "lower_third_duration_s",
    }


def test_every_spec_has_type_and_applies():
    for key, spec in load_tunables().items():
        assert spec["type"] in {"number", "int", "bool", "color", "enum"}, key
        assert spec["applies"] in {"window", "middle_third", "lower_third", "both"}, key
        if spec["type"] == "enum":
            assert spec["values"], key


def test_unknown_key_is_dropped():
    assert safe_overrides({"totally_unknown": 5}) == {}


def test_numeric_string_coerces_to_number():
    assert safe_overrides({"phrase_max_words": "3"}) == {"phrase_max_words": 3}


def test_out_of_range_is_clamped_low():
    assert safe_overrides({"reel_seconds": 5}) == {"reel_seconds": 15}


def test_out_of_range_is_clamped_high():
    assert safe_overrides({"font_scale": 9.0}) == {"font_scale": 2.0}


def test_bad_enum_is_dropped():
    assert safe_overrides({"anim_style": "wobble"}) == {}


def test_valid_enum_is_kept():
    assert safe_overrides({"anim_style": "fade"}) == {"anim_style": "fade"}


def test_bad_color_is_dropped():
    assert safe_overrides({"overlay_accent": "#GG0000"}) == {}


def test_valid_color_is_kept():
    assert safe_overrides({"overlay_accent": "#00E5FF"}) == {"overlay_accent": "#00E5FF"}


def test_bool_string_coerces_to_bool():
    assert safe_overrides({"phrase_uppercase": "true"}) == {"phrase_uppercase": True}
    assert safe_overrides({"phrase_uppercase": "false"}) == {"phrase_uppercase": False}


def test_int_type_rejects_non_bool_and_keeps_int():
    # booleans must never masquerade as ints
    assert safe_overrides({"accent_bar_px": True}) == {}
    assert safe_overrides({"accent_bar_px": 12}) == {"accent_bar_px": 12}


def test_non_dict_input_is_empty():
    assert safe_overrides(None) == {}
    assert safe_overrides([1, 2, 3]) == {}


def test_mixed_batch_drops_only_the_bad_ones():
    out = safe_overrides(
        {
            "phrase_max_words": 3,
            "overlay_accent": "#00E5FF",
            "anim_style": "nope",         # dropped
            "totally_unknown": 1,          # dropped
            "reel_seconds": 60,
        }
    )
    assert out == {
        "phrase_max_words": 3,
        "overlay_accent": "#00E5FF",
        "reel_seconds": 60.0,
    }


# ── property: output is always a valid subset in-bounds (Hypothesis) ──
@given(
    st.dictionaries(
        keys=st.one_of(st.sampled_from(list(load_tunables().keys())), st.text()),
        values=st.one_of(
            st.integers(min_value=-1000, max_value=1000),
            st.floats(allow_nan=False, allow_infinity=False, min_value=-1e4, max_value=1e4),
            st.text(),
            st.booleans(),
            st.none(),
        ),
    )
)
def test_output_keys_subset_and_values_in_bounds(raw):
    tun = load_tunables()
    out = safe_overrides(raw)
    assert set(out).issubset(set(tun))
    for key, value in out.items():
        spec = tun[key]
        t = spec["type"]
        if t in ("number", "int"):
            assert spec["min"] <= value <= spec["max"]
            if t == "int":
                assert isinstance(value, int) and not isinstance(value, bool)
        elif t == "bool":
            assert isinstance(value, bool)
        elif t == "color":
            assert _HEX.match(value)
        elif t == "enum":
            assert value in spec["values"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
