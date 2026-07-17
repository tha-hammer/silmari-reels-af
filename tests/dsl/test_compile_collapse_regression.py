"""B3 — metamorphic regression for ``bd ate`` (AF-9ja).

The exact defect shape: N distinct timecoded composite lines over a duplicate-phrase
``words: []`` source. The compiled plan must have N distinct, strictly-increasing
source spans (injectivity + monotonicity), not one clip repeated.
"""

from pathlib import Path

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import SourceRef, SourceSegment

FIX = Path(__file__).resolve().parent / "fixtures"


def test_bd_ate_distinct_timecodes_yield_distinct_spans():
    doc = read_composite(
        (FIX / "collapse_repro.ts.md").read_text(),
        source_path=FIX / "collapse_repro.ts.md",
    )
    words = load_words(FIX / "collapse_repro.words.json")
    res = compile_composite(doc, words, SourceRef(source_url="https://example.com/s.mp4"))
    # The invariant under test is 3 distinct increasing spans — not the ok/warning
    # distinction — so a benign compile warning must not fail the assertion.
    assert res.status in ("ok", "warning") and res.plan is not None
    src = [s for s in res.plan.segments if isinstance(s, SourceSegment)]
    starts = [s.start_s for s in src]
    assert len(src) == 3
    assert len({(s.start_s, s.end_s) for s in src}) == 3  # injective
    assert starts == sorted(starts) and len(set(starts)) == 3  # strictly increasing
