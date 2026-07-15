"""B4 → CompileContext supplies what .ts.md + words alone cannot.

The context carries the data the contract review said cannot be inferred from the
composite and words sidecar: source URL/video id, delivery policy, vertical
geometry, hook duration bounds, render defaults, and cut-in metadata (research C27).

D1 compat contract: ``context`` is an OPTIONAL keyword-only arg. ``context=None``
(the default) reproduces today's behavior exactly — proven by the whole
``tests/dsl/`` suite staying green with no edits, NOT by an in-test tautology.
"""

from __future__ import annotations

import pytest

from reel_af.dsl.compile import compile_composite
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    A1_MAX_HOOK_CLIP_S,
    A1_MIN_HOOK_CLIP_S,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DSL_HOOKS_WORKFLOW,
    CompileContext,
    CutInSpec,
    SourceRef,
)

A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"


def _doc():
    """Text must align to tests/dsl/fixtures/source.words.json (the aligner is real)."""
    return read_composite(
        "00:00:04.120  They don't reason. They pattern-match at a scale that"
        " feels like reasoning.\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )


def test_compile_context_supplies_non_inferable_data(source_words_sidecar):
    ctx = CompileContext(
        source_url=A1_SOURCE_URL,
        video_id="abc123",
        delivery_required=True,
        cut_ins=[
            CutInSpec(type="zoom", at_s=0.5, until_s=1.5, line="hook", zoom_focus="upper")
        ],
    )

    assert ctx.workflow == DSL_HOOKS_WORKFLOW
    assert ctx.canvas_width == CANVAS_WIDTH
    assert ctx.canvas_height == CANVAS_HEIGHT
    assert ctx.min_hook_clip_s == A1_MIN_HOOK_CLIP_S
    assert ctx.max_hook_clip_s == A1_MAX_HOOK_CLIP_S
    assert ctx.cut_ins[0].zoom_focus == "upper"


def test_compile_accepts_context_and_still_compiles(source_words_sidecar):
    ctx = CompileContext(source_url=A1_SOURCE_URL, video_id="abc123")
    result = compile_composite(
        _doc(), source_words_sidecar, SourceRef(source_url=A1_SOURCE_URL), context=ctx
    )

    assert result.status != "error", [d.code for d in result.diagnostics]
    assert result.plan is not None
    assert result.plan.source_url == A1_SOURCE_URL


def test_context_is_keyword_only_and_optional(source_words_sidecar):
    """D1: the arg is keyword-only, so no positional caller can be broken by it."""
    with pytest.raises(TypeError):
        compile_composite(  # type: ignore[misc]
            _doc(),
            source_words_sidecar,
            SourceRef(source_url=A1_SOURCE_URL),
            CompileContext(source_url=A1_SOURCE_URL),
        )


def test_compile_context_forbids_extra_fields():
    with pytest.raises(ValueError):
        CompileContext(source_url=A1_SOURCE_URL, bogus="nope")  # type: ignore[call-arg]


def test_cutin_spec_rejects_inverted_window():
    with pytest.raises(ValueError):
        CutInSpec(type="zoom", at_s=5.0, until_s=5.0)


def test_visual_cutin_spec_requires_image_prompt():
    with pytest.raises(ValueError):
        CutInSpec(type="visual", at_s=1.0, until_s=2.0)
