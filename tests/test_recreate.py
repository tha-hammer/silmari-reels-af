"""Plan 2 — Recreate loop + cost guard (backend policy layer).

Tests the pure policy layer in ``reel_af.recreate``: prompt+note composition,
HQ model selection, single-slide replace, sibling-safety, premium-ack guard,
and the per-carousel HQ-recreate cap. Built on Plan 1's ``regenerate_slide``
primitive (consumed, injected as a fake here) and a fake ``StoragePort``.

Behaviors land incrementally as Plan 1's seam (``regenerate_slide``) arrives;
Behavior 1 (compose) is pure and independent.
"""

import pytest

from reel_af.recreate import compose_recreate_prompt


def test_compose_puts_original_then_note():
    composed = compose_recreate_prompt("a quiet lab bench", "make it night, add neon")
    assert "a quiet lab bench" in composed
    assert "make it night, add neon" in composed
    assert composed.index("a quiet lab bench") < composed.index("make it night, add neon")


@pytest.mark.parametrize(
    "original,note",
    [
        ("orig", "note"),
        ("café ☕ scene", "add lumière"),
        ("line one\nline two", "note\nwith newline"),
        ("short", "n " * 5000),
    ],
)
def test_compose_preserves_both_substrings_in_order(original, note):
    composed = compose_recreate_prompt(original, note)
    assert original in composed and note in composed
    assert composed.index(original) < composed.index(note)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_compose_rejects_blank_note(bad):
    with pytest.raises(ValueError, match="note"):
        compose_recreate_prompt("orig", bad)
