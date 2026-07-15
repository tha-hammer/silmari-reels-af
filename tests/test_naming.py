"""B2: reel_output_name — pure descriptive-filename derivation (no I/O, injected date)."""

from __future__ import annotations

import re
from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from reel_af.naming import reel_output_name

WHEN = date(2026, 7, 14)


def test_basic_slug_date_runid():
    assert reel_output_name("The Hook, Explained!", "abc123", WHEN) == (
        "the-hook-explained-20260714-abc123.mp4"
    )


def test_empty_or_none_or_blank_falls_back():
    assert reel_output_name("", "abc123", WHEN) == "reel-20260714-abc123.mp4"
    assert reel_output_name(None, "abc123", WHEN) == "reel-20260714-abc123.mp4"
    assert reel_output_name("   ", "abc123", WHEN) == "reel-20260714-abc123.mp4"
    assert reel_output_name("!!! ---", "abc123", WHEN) == "reel-20260714-abc123.mp4"


def test_unicode_transliterated_to_ascii():
    assert reel_output_name("Café déjà vu", "abc123", WHEN) == "cafe-deja-vu-20260714-abc123.mp4"


def test_dashes_and_case_normalized():
    assert reel_output_name("--Already-SLUGGED--", "r0", WHEN) == "already-slugged-20260714-r0.mp4"


def test_long_source_is_capped_no_trailing_dash():
    out = reel_output_name("word " * 60, "abc123", WHEN)
    assert out.endswith("-20260714-abc123.mp4")
    assert "--" not in out and "-." not in out
    # the slug portion never exceeds the cap
    slug = out[: -len("-20260714-abc123.mp4")]
    assert len(slug) <= reel_output_name.CAP


def test_deterministic():
    a = reel_output_name("Same Input", "run1", WHEN)
    b = reel_output_name("Same Input", "run1", WHEN)
    assert a == b


@given(st.text())
def test_output_always_safe(source):
    out = reel_output_name(source, "r0", WHEN)
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*-\d{8}-r0\.mp4", out), out
    assert "--" not in out
