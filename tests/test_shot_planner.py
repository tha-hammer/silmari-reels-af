"""Unit tests for the deterministic shot planner.

These tests are load-bearing: the planner is pure code on the critical
timing path, so every break condition gets its own focused fixture.
"""

from __future__ import annotations

import pytest

from reel_af.v2.models import WordTiming
from reel_af.v2.planning.font_metrics import MAX_CHARS_PER_LINE, measured_width
from reel_af.v2.planning.shot_planner import (
    MAX_WIDTH_PER_CARD,
    group_into_shots,
    pack_cards,
)

# ───── helpers ───────────────────────────────────────────────────────


def make_words(specs: list[tuple[str, float, float]]) -> list[WordTiming]:
    """Tuple-of-(word, start, end) → list[WordTiming]. Keeps fixtures terse."""
    return [WordTiming(word=w, start_s=s, end_s=e) for (w, s, e) in specs]


def make_card(words: list[WordTiming]):
    """Build a Card via the planner so line_count is computed consistently."""
    # We import lazily so the test file works even if Card is restructured.
    from reel_af.v2.models import Card
    from reel_af.v2.planning.font_metrics import line_count

    text = " ".join(w.word for w in words)
    return Card(
        text=text,
        words=words,
        start_s=words[0].start_s,
        end_s=words[-1].end_s,
        line_count=line_count(text),
    )


# ───── pack_cards ────────────────────────────────────────────────────


def test_pack_cards_empty() -> None:
    assert pack_cards([]) == []


def test_pack_cards_5_word_cap() -> None:
    """Six short words, contiguous (no gaps, no punctuation) → 5+1 split."""
    words = make_words(
        [
            ("one", 0.0, 0.10),
            ("two", 0.10, 0.20),
            ("three", 0.20, 0.30),
            ("four", 0.30, 0.40),
            ("five", 0.40, 0.50),
            ("six", 0.50, 0.60),
        ]
    )
    cards = pack_cards(words)
    # Tail-merge rule would fold a lone "six" back into the prev card if it
    # fits — but the prev card is already at the 5-word cap so no merge.
    assert len(cards) == 2
    assert cards[0].text == "one two three four five"
    assert len(cards[0].words) == 5
    assert cards[1].text == "six"


def test_pack_cards_clause_boundary() -> None:
    """Clause-snap fires on comma when card has ≥ 2 words; not on lone word."""
    words = make_words(
        [
            ("wait", 0.0, 0.20),
            ("for,", 0.20, 0.40),  # comma — but card len is 2, eligible to snap
            ("it.", 0.40, 0.60),
        ]
    )
    cards = pack_cards(words)
    # Expect break after "for,"; then trailing "it." is folded back since
    # the merged 3-word card fits comfortably. So the result is one card.
    assert len(cards) == 1
    assert cards[0].text == "wait for, it."

    # Now verify the snap actually fired mid-walk: feed a longer sequence
    # where the post-clause fragment is long enough that no merge happens.
    words2 = make_words(
        [
            ("hey", 0.0, 0.20),
            ("you,", 0.20, 0.40),
            ("listen", 0.40, 0.70),
            ("up", 0.70, 0.90),
            ("now", 0.90, 1.10),
        ]
    )
    cards2 = pack_cards(words2)
    assert len(cards2) == 2
    assert cards2[0].text == "hey you,"
    assert cards2[1].text == "listen up now"


def test_pack_cards_no_clause_break_on_single_word() -> None:
    """If a clause punct ends the FIRST word of a card, do NOT break (len<2)."""
    words = make_words(
        [
            ("yes.", 0.0, 0.20),  # punct, but only 1 word in card → no break
            ("but", 0.20, 0.40),
            ("why", 0.40, 0.60),
        ]
    )
    cards = pack_cards(words)
    # All three fit in one card (no gaps, no width overflow, no >=2-word punct).
    assert len(cards) == 1
    assert cards[0].text == "yes. but why"


def test_pack_cards_gap_split() -> None:
    """Gap > 0.20s between word 2 and word 3 → card breaks after word 2."""
    words = make_words(
        [
            ("hello", 0.0, 0.30),
            ("there", 0.30, 0.60),
            ("friend", 0.90, 1.20),  # 0.30s gap from prev end → exceeds 0.20s
        ]
    )
    cards = pack_cards(words)
    assert len(cards) == 2
    assert cards[0].text == "hello there"
    assert cards[1].text == "friend"


def test_pack_cards_small_gap_no_split() -> None:
    """Gap of 0.10s (well under threshold) does NOT trigger a split."""
    words = make_words(
        [
            ("hello", 0.0, 0.30),
            ("there", 0.30, 0.60),
            ("friend", 0.70, 1.00),  # 0.10s gap → no split
        ]
    )
    cards = pack_cards(words)
    assert len(cards) == 1
    assert cards[0].text == "hello there friend"


def test_pack_cards_long_word_alone() -> None:
    """Single long word exceeding MAX_CHARS_PER_LINE → one card with that word."""
    long_word = "M" * 30  # measured_width ≈ 46.5 > 25, but ≤ 47.5
    assert measured_width(long_word) > MAX_CHARS_PER_LINE
    words = make_words([(long_word, 0.0, 0.50)])
    cards = pack_cards(words)
    assert len(cards) == 1
    assert cards[0].text == long_word
    assert cards[0].words[0].word == long_word


def test_pack_cards_char_overflow_cap() -> None:
    """Wide words trigger width/line-count breaks before the 5-word cap.

    Each 'WWWWWWWWW' (9 W's) measures ~13.95. Two fit on one line (~28.45
    chars). A third would wrap to a 3rd line — the planner must break before
    that. So 5 such words yield 3 cards of [2, 2, 1] words — NOT one card of
    5. This proves the structural cap fires earlier than the 5-word cap.
    """
    token = "W" * 9
    words = make_words(
        [
            (token, 0.0, 0.20),
            (token, 0.20, 0.40),
            (token, 0.40, 0.60),
            (token, 0.60, 0.80),
            (token, 0.80, 1.00),
        ]
    )
    # Sanity: 4 of these are already over the width cap.
    assert measured_width(" ".join([token] * 4)) > MAX_WIDTH_PER_CARD
    cards = pack_cards(words)
    # The structural caps fired well before the 5-word cap would have.
    assert len(cards) >= 2
    assert all(len(c.words) < 5 for c in cards)
    # All words preserved in order.
    flat = [w for c in cards for w in c.words]
    assert len(flat) == 5


# ───── group_into_shots ──────────────────────────────────────────────


def test_group_into_shots_empty() -> None:
    assert group_into_shots([]) == []


def test_group_into_shots_single_card() -> None:
    """1-card reel collapses to 1 shot with role='hook'."""
    card = make_card(make_words([("hi", 0.0, 0.50)]))
    shots = group_into_shots([card])
    assert len(shots) == 1
    assert shots[0].role == "hook"
    assert shots[0].veo_duration == 4  # 0.5 + 1.0 = 1.5 → bucket 4


def test_group_into_shots_4_card_cap() -> None:
    """Five contiguous 1s cards → first shot has 4 cards, second has 1."""
    cards = []
    for i in range(5):
        cards.append(make_card(make_words([(f"w{i}", float(i), float(i) + 1.0)])))
    shots = group_into_shots(cards)
    assert len(shots) == 2
    assert len(shots[0].cards) == 4
    assert len(shots[1].cards) == 1


def test_group_into_shots_7s_cap() -> None:
    """Three 3.0s cards → first shot holds 2 (6s ≤ 7s), second holds 1."""
    cards = [
        make_card(make_words([("a", 0.0, 3.0)])),
        make_card(make_words([("b", 3.0, 6.0)])),
        make_card(make_words([("c", 6.0, 9.0)])),
    ]
    shots = group_into_shots(cards)
    assert len(shots) == 2
    assert len(shots[0].cards) == 2
    assert shots[0].duration_s == pytest.approx(6.0)
    assert len(shots[1].cards) == 1
    assert shots[1].duration_s == pytest.approx(3.0)


def test_group_roles_assigned() -> None:
    """Three shots → hook, mechanism, payoff in order."""
    # Build 9 cards of 1.0s each. With 4-card cap that's 3 shots of 4/4/1.
    cards = [
        make_card(make_words([(f"w{i}", float(i), float(i) + 1.0)]))
        for i in range(9)
    ]
    shots = group_into_shots(cards)
    assert len(shots) == 3
    assert shots[0].role == "hook"
    assert shots[1].role == "mechanism"
    assert shots[2].role == "payoff"
    # idx fields wired correctly:
    assert [s.idx for s in shots] == [0, 1, 2]


def test_veo_duration_buckets() -> None:
    """Smallest Veo bucket ≥ duration_s + 1.0s safety."""
    # Build single-card shots of various durations.
    def shot_for(dur: float):
        card = make_card(make_words([("x", 0.0, dur)]))
        shots = group_into_shots([card])
        return shots[0]

    assert shot_for(2.5).veo_duration == 4  # 2.5+1.0=3.5 → 4
    assert shot_for(4.5).veo_duration == 6  # 4.5+1.0=5.5 → 6
    assert shot_for(6.5).veo_duration == 8  # 6.5+1.0=7.5 → 8
    # Edge: exactly 3.0s → 3+1=4, bucket 4. 5.0s → 6. 7.0s → 8.
    assert shot_for(3.0).veo_duration == 4
    assert shot_for(5.0).veo_duration == 6
    assert shot_for(7.0).veo_duration == 8


def test_group_into_shots_timing_bounds() -> None:
    """Shot start/end/duration must equal first-card start, last-card end."""
    cards = [
        make_card(make_words([("a", 0.5, 1.5)])),
        make_card(make_words([("b", 1.5, 2.5)])),
    ]
    shots = group_into_shots(cards)
    assert len(shots) == 1
    assert shots[0].start_s == pytest.approx(0.5)
    assert shots[0].end_s == pytest.approx(2.5)
    assert shots[0].duration_s == pytest.approx(2.0)
