"""B12 - CP→DB status normalization is total; unknown → failed."""

from __future__ import annotations

import pytest
from reel_jobs import TERMINAL_STATUSES, normalize_reel_status


@pytest.mark.parametrize(
    ("cp_status", "expected"),
    [
        ("queued", "queued"), ("pending", "queued"), ("registered", "queued"),
        ("submitted", "queued"),
        ("running", "producing"), ("processing", "producing"), ("ingesting", "producing"),
        ("transcribing", "producing"), ("rendering", "producing"), ("compositing", "producing"),
        ("waiting", "producing"), ("paused", "producing"),
        ("succeeded", "succeeded"), ("success", "succeeded"), ("completed", "succeeded"),
        ("done", "succeeded"), ("ok", "succeeded"),
        ("failed", "failed"), ("error", "failed"), ("timeout", "failed"), ("unknown", "failed"),
        ("cancelled", "cancelled"), ("canceled", "cancelled"), ("cancel", "cancelled"),
    ],
)
def test_status_families(cp_status, expected):
    assert normalize_reel_status(cp_status) == expected


def test_status_is_case_and_whitespace_insensitive():
    assert normalize_reel_status("  SUCCEEDED ") == "succeeded"


@pytest.mark.parametrize("bad", [None, "", "weird-status", "42", 7])
def test_unparseable_status_maps_to_failed(bad):
    assert normalize_reel_status(bad) == "failed"


def test_terminal_set():
    assert TERMINAL_STATUSES == frozenset({"succeeded", "failed", "cancelled"})
