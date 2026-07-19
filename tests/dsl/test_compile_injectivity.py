"""B2 - compile-stage aligned span injectivity verifier (fail closed).

N distinct composite segments must yield N distinct source spans. A collapse
means the aligner mapped several segments onto one cue (the ``bd ate`` defect);
compile must refuse rather than forward a degenerate plan to render.
"""

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from reel_af.dsl.compile import (
    _AlignedSegment,
    _verify_injective_spans,
    compile_composite,
    load_words,
)
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import Diagnostic, SourceRef

FIX = Path(__file__).resolve().parent / "fixtures"


class _Seg:  # minimal stand-in for CompositeSegment (index/timecode_s/source used in msgs)
    def __init__(self, index, timecode_s):
        self.index, self.timecode_s, self.source = index, timecode_s, None


def _aligned(spans):
    return [_AlignedSegment(_Seg(i, s), s, e, "t") for i, (s, e) in enumerate(spans)]


def test_distinct_increasing_spans_pass():
    diags: list[Diagnostic] = []
    assert _verify_injective_spans(_aligned([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]), diags) is False
    assert diags == []


def test_identical_spans_flagged_and_fail_closed():
    diags: list[Diagnostic] = []
    assert _verify_injective_spans(_aligned([(1.0, 2.0), (1.0, 2.0), (1.0, 2.0)]), diags) is True
    assert [d.code for d in diags] == ["SEGMENT_SPAN_COLLAPSE"]
    assert diags[0].severity == "error" and diags[0].context.get("kind") == "injectivity"


def test_distinct_reordered_spans_pass():
    diags: list[Diagnostic] = []
    assert _verify_injective_spans(_aligned([(20.0, 24.0), (10.0, 14.0), (30.0, 34.0)]), diags) is False
    assert diags == []


def test_single_and_empty_are_ok():
    assert _verify_injective_spans(_aligned([(1.0, 2.0)]), []) is False
    assert _verify_injective_spans(_aligned([]), []) is False


@given(
    starts=st.lists(
        st.floats(min_value=0, max_value=1e5, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=12,
        unique=True,
    )
)
def test_distinct_increasing_never_false_positive(starts):
    starts = sorted(starts)
    aligned = _aligned([(s, s + 0.5) for s in starts])
    assert _verify_injective_spans(aligned, []) is False


def test_end_to_end_fail_closed_on_genuine_collapse():
    # A genuinely degenerate input: two distinct composite segments whose only
    # matching cue is the same single source cue → identical spans. Compile must
    # fail closed with SEGMENT_SPAN_COLLAPSE, never forward a degenerate plan.
    doc = read_composite(
        (FIX / "collapse_forced.ts.md").read_text(),
        source_path=FIX / "collapse_forced.ts.md",
    )
    words = load_words(FIX / "collapse_forced.words.json")
    res = compile_composite(doc, words, SourceRef(source_url="https://example.com/s.mp4"))
    assert res.status == "error" and res.plan is None
    assert any(d.code == "SEGMENT_SPAN_COLLAPSE" for d in res.diagnostics)
