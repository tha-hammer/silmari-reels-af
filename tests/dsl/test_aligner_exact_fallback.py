"""Aligner: exact-cue rescue for degenerate/short segments (caption-source).

Reproduces the live E2E failure: a composite segment that is a sub-3-char filler
("uh") produces no character trigrams, so `_trigram_cosine` scores 0.0 against
every caption cue → below_floor → the whole compile fails, even though the cue
exists verbatim in the transcript. The exact-token rescue (mirroring the word
path's `_find_exact_run`) places it against the real cue, disambiguated by the
composite segment's timecode.
"""

from __future__ import annotations

from reel_af.dsl.aligner import align
from reel_af.dsl.models import FallbackSegment, WordsSidecar


def _side(*cues: tuple[str, float, float]) -> WordsSidecar:
    return WordsSidecar(
        words=[], segments=[FallbackSegment(text=t, start_s=s, end_s=e) for t, s, e in cues]
    )


def test_short_filler_aligns_via_exact_cue_when_trigram_is_empty():
    # "uh" -> zero trigrams -> the pre-fix path scored 0.00 against all cues.
    side = _side(
        ("So, as as I go through my week,", 444.9, 448.88),
        ("uh", 447.919, 450.16),
        ("I say it all the time", 448.88, 451.88),
    )
    r = align("uh", side, timecode_s=447.92)
    assert r.kind == "aligned"
    assert r.method == "cue_exact"
    assert (r.start_s, r.end_s) == (447.919, 450.16)
    assert r.quality == 1.0


def test_exact_cue_is_disambiguated_by_nearest_timecode():
    side = _side(
        ("uh well anyway", 5.0, 7.0),  # decoy "uh" early in the source
        ("something else entirely here", 100.0, 103.0),
        ("uh", 447.919, 450.16),  # the correct one, near the composite timecode
    )
    r = align("uh", side, timecode_s=447.92)
    assert r.method == "cue_exact"
    assert (r.start_s, r.end_s) == (447.919, 450.16)
    # With no positional prior, the earliest occurrence wins (deterministic).
    r0 = align("uh", side)
    assert (r0.start_s, r0.end_s) == (5.0, 7.0)


def test_trigram_match_still_wins_over_exact_rescue():
    # A normal, distinctive query aligns via the existing trigram path unchanged;
    # the exact rescue only fires when trigram fails.
    side = _side(
        ("the physics of black holes and how", 1.8, 3.4),
        ("uh", 10.0, 11.0),
    )
    r = align("the physics of black holes and how", side, timecode_s=2.0)
    assert r.method == "cue_fallback"
    assert r.quality >= 0.85


def test_short_query_absent_from_all_cues_stays_unmatched():
    side = _side(("completely different words here", 0.0, 2.0))
    r = align("qq", side, timecode_s=1.0)  # "qq": no trigrams AND not a token anywhere
    assert r.kind == "unmatched"
    assert r.reason == "below_floor"
