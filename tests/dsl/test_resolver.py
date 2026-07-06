from __future__ import annotations

import pytest

from reel_af.dsl.models import HoleChoice, HoleContext, ResolveResult
from reel_af.dsl.parser import parse_marker
from reel_af.dsl.resolver import ResolveError, resolve_file, resolve_text


def test_resolve_text_fills_transition_hole_and_preserves_exclude():
    contexts: list[HoleContext] = []

    def choose(context: HoleContext) -> HoleChoice:
        contexts.append(context)
        assert context.field_name == "primitive"
        assert context.before_text == "before"
        assert context.after_text == "after"
        assert context.domain.name == "primitive"
        assert context.domain.excluded == ("dissolve",)
        assert "dissolve" not in context.domain.candidates
        return HoleChoice(value="smoothleft")

    result = resolve_text("before\n[trans ? 1.0 exclude=dissolve]\nafter\n", choose)

    assert isinstance(result, ResolveResult)
    assert result.changed is True
    assert result.text == "before\n[trans ? 1.0 exclude=dissolve => smoothleft]\nafter\n"
    assert result.choices == [HoleChoice(value="smoothleft")]
    assert len(contexts) == 1

    marker = parse_marker("[trans ? 1.0 exclude=dissolve => smoothleft]")
    assert marker.primitive.resolution == "smoothleft"


def test_resolve_text_passes_positive_duration_domain_for_insert_relevant():
    def choose(context: HoleContext) -> HoleChoice:
        assert context.field_name == "duration_s"
        assert context.domain.name == "duration_s"
        assert context.domain.min_value == pytest.approx(0.001)
        return HoleChoice(value=30)

    result = resolve_text("[insert relevant ?]", choose)

    assert result.text == "[insert relevant ? => 30]"


def test_resolve_text_is_idempotent_for_already_resolved_hole():
    text = "[trans ? 1.0 exclude=dissolve => smoothleft]"

    result = resolve_text(text, lambda _context: pytest.fail("choice should not be called"))

    assert result.changed is False
    assert result.text == text
    assert result.choices == []


def test_resolve_text_rejects_excluded_choice():
    with pytest.raises(ResolveError, match="excluded"):
        resolve_text(
            "before\n[trans ? 1.0 exclude=dissolve]\nafter\n",
            lambda _context: HoleChoice(value="dissolve"),
        )


def test_resolve_file_writes_atomically_and_preserves_unrelated_text(tmp_path):
    path = tmp_path / "clip.ts.md"
    path.write_text("before\n[trans ? 1.0 exclude=dissolve]\nafter\n", encoding="utf-8")

    result = resolve_file(path, lambda _context: HoleChoice(value="smoothleft"))

    assert result.changed is True
    assert path.read_text(encoding="utf-8") == (
        "before\n[trans ? 1.0 exclude=dissolve => smoothleft]\nafter\n"
    )
