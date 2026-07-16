"""B3 → workflow-scoped marker rejection (DSL-hooks workflow only).

D3: UNSUPPORTED_INSERT / UNSUPPORTED_FIND are VESTIGIAL on the default workflow.
``[insert relevant]``, ``[insert file]`` and ``[find relevant]`` are supported,
tested features — tests/dsl/test_compile_unsupported.py pins that and MUST stay
green. What this slice adds is a per-workflow rejection policy, gated on
CompileContext.workflow. ``context is None`` -> today's behavior, byte-for-byte.

Depends on B4 (CompileContext must exist before this Red can be written).
"""

from __future__ import annotations

import pytest

from reel_af.dsl.compile import compile_composite
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    DEFAULT_WORKFLOW,
    DSL_HOOKS_WORKFLOW,
    CompileContext,
    SourceRef,
)

A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"

_SEG_A = "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning."
_SEG_B = "00:00:21.740  And the moment you trust the feeling, you ship the bug."


def _source() -> SourceRef:
    return SourceRef(source_url=A1_SOURCE_URL)


def _dsl_hooks_context() -> CompileContext:
    return CompileContext(source_url=A1_SOURCE_URL, workflow=DSL_HOOKS_WORKFLOW)


@pytest.mark.parametrize(
    "marker,expected_code",
    [
        ("[insert file rel_01]", "UNSUPPORTED_INSERT"),
        ("[find relevant 30 x5]", "UNSUPPORTED_FIND"),
    ],
)
def test_unsupported_marker_stops_render_on_dsl_hooks_workflow(
    marker, expected_code, source_words_sidecar
):
    doc = read_composite(f"{_SEG_A}\n\n{marker}\n\n{_SEG_B}\n")

    result = compile_composite(
        doc, source_words_sidecar, _source(), context=_dsl_hooks_context()
    )

    assert result.status == "error"
    assert expected_code in {d.code for d in result.diagnostics}
    assert result.plan is None


def test_insert_relevant_is_unsupported_on_dsl_hooks_workflow(source_words_sidecar):
    """DSL-hooks sources footage from the A1 video only — no relevant-corpus inserts."""
    doc = read_composite(f"{_SEG_A}\n\n[insert relevant 5]\n\n{_SEG_B}\n")

    result = compile_composite(
        doc, source_words_sidecar, _source(), context=_dsl_hooks_context()
    )

    assert result.status == "error"
    assert "UNSUPPORTED_INSERT" in {d.code for d in result.diagnostics}


def test_no_context_leaves_default_workflow_untouched(source_words_sidecar):
    """The compat contract: [insert file] is a SUPPORTED feature by default (D3)."""
    doc = read_composite(f"{_SEG_A}\n\n[find relevant 30 x5]\n\n{_SEG_B}\n")

    result = compile_composite(doc, source_words_sidecar, _source())

    assert "UNSUPPORTED_FIND" not in {d.code for d in result.diagnostics}


def test_explicit_default_workflow_context_also_leaves_it_untouched(source_words_sidecar):
    """Rejection is keyed by workflow, not merely by context presence."""
    doc = read_composite(f"{_SEG_A}\n\n[find relevant 30 x5]\n\n{_SEG_B}\n")

    result = compile_composite(
        doc,
        source_words_sidecar,
        _source(),
        context=CompileContext(source_url=A1_SOURCE_URL, workflow=DEFAULT_WORKFLOW),
    )

    assert "UNSUPPORTED_FIND" not in {d.code for d in result.diagnostics}


def test_supported_markers_still_compile_on_dsl_hooks_workflow(source_words_sidecar):
    """Only the unsupported VERBS are rejected — trans/extend/join/insert-black are fine."""
    doc = read_composite(f"{_SEG_A}\n\n[trans dissolve 0.8]\n\n{_SEG_B}\n")

    result = compile_composite(
        doc, source_words_sidecar, _source(), context=_dsl_hooks_context()
    )

    assert result.status != "error", [d.code for d in result.diagnostics]
    assert result.plan is not None
