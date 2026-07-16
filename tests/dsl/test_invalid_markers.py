"""B2 → malformed markers survive read_composite and emit INVALID_MARKER.

MOTIVATION — this closes a latent SILENT-FAILURE defect. Today read_composite
swallows MarkerError in BOTH marker paths (composite.py, inline + standalone) and
never appends the marker, so a typo'd marker is dropped on the floor: the
transition never happens, the reel renders wrong, and NOTHING reports it.
compile_composite cannot diagnose what it cannot see, which is why B2 lives in
composite.py + models.py, not compile.py.

D2: the code is INVALID_MARKER (models.py), not DSL_MARKER_INVALID. DiagnosticCode
is a Literal[...] type alias, not an Enum — emission uses a bare string literal at
the site, matching the existing idiom (cf. compile.py code="UNRESOLVED_HOLE").

N-1: the parametrize cases below are all VERIFIED to raise MarkerError with
markers=0. Notably ``[insert relevant ? => nope 5]`` is NOT one of them — ``? =>
value`` is valid DSL v2 (a resolved hole, the resolver's own audit-trail format),
it parses cleanly and lands in doc.markers.
"""

from __future__ import annotations

import pytest

from reel_af.dsl.compile import compile_composite
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import SourceRef

A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"

_SEG_A = "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning."
_SEG_B = "00:00:21.740  And the moment you trust the feeling, you ship the bug."

# Every case here (a) MATCHES the marker regex so read_composite calls
# parse_marker, and (b) raises MarkerError there — i.e. it is swallowed today.
# Both conditions are required: a case that fails (a) is never seen as a marker
# at all and is undiagnosable without a heuristic scanner (see the `[]` and
# `[insert` tests below).
MALFORMED_MARKERS = [
    "[bogus 1.0]",     # unknown verb
    "[trans 1.0]",     # trans without a primitive
    "[extend sideways 0.5]",  # unknown edge selector
]


def _source() -> SourceRef:
    return SourceRef(source_url=A1_SOURCE_URL)


@pytest.mark.parametrize("marker", MALFORMED_MARKERS)
def test_malformed_marker_survives_read_composite(marker):
    doc = read_composite(f"{_SEG_A}\n\n{marker}\n\n{_SEG_B}\n")

    assert doc.invalid_markers, f"{marker!r} was swallowed — the silent-drop defect"
    assert doc.invalid_markers[0].text == marker
    assert doc.invalid_markers[0].source.line > 0
    assert doc.markers == []  # it is NOT a valid marker


@pytest.mark.parametrize("marker", MALFORMED_MARKERS)
def test_malformed_marker_emits_invalid_marker_diagnostic(marker, source_words_sidecar):
    doc = read_composite(f"{_SEG_A}\n\n{marker}\n\n{_SEG_B}\n")

    result = compile_composite(doc, source_words_sidecar, _source())

    assert result.status == "error"
    assert "INVALID_MARKER" in {d.code for d in result.diagnostics}
    assert result.plan is None


def test_malformed_inline_marker_also_survives():
    """The inline path (composite.py) swallows separately from the standalone path."""
    doc = read_composite(f"{_SEG_A} [bogus 1.0]\n\n{_SEG_B}\n")

    assert doc.invalid_markers
    assert doc.invalid_markers[0].text == "[bogus 1.0]"


def test_resolved_hole_marker_is_valid_and_not_flagged():
    """N-1 guard: '? => value' is valid DSL v2 (resolved hole), never an invalid marker."""
    doc = read_composite(f"{_SEG_A}\n\n[trans ? 1.0 exclude=fadeblack => smoothleft]\n\n{_SEG_B}\n")

    assert doc.invalid_markers == []
    assert len(doc.markers) == 1


def test_valid_markers_are_unaffected(source_words_sidecar):
    doc = read_composite(f"{_SEG_A}\n\n[trans dissolve 0.8]\n\n{_SEG_B}\n")

    assert doc.invalid_markers == []
    result = compile_composite(doc, source_words_sidecar, _source())
    assert result.status != "error", [d.code for d in result.diagnostics]


@pytest.mark.parametrize("text", ["[insert", "[]"])
def test_text_that_never_matches_the_marker_regex_is_not_diagnosable(text):
    """Documented non-goal, and the boundary of what B2 can close.

    The marker regexes require a non-empty bracket body (``\\[[^\\]]+\\]``), so
    neither an unclosed ``[insert`` nor an empty ``[]`` is ever recognised as a
    marker — read_composite never calls parse_marker on them, so there is no
    MarkerError to preserve. Diagnosing these would need a heuristic
    "looks-like-a-broken-marker" scanner, which is explicitly out of scope.

    (Note: ``parse_marker("[]")`` DOES raise when called directly — but that path
    is unreachable from read_composite. Testing parse_marker in isolation would
    prove the wrong thing.)
    """
    doc = read_composite(f"{_SEG_A}\n\n{text}\n\n{_SEG_B}\n")

    assert doc.invalid_markers == []
