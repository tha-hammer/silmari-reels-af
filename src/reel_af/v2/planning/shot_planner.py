"""Deterministic shot planner — word timings → cards → shots, no LLM.

Algorithm (full version in docs/ARCHITECTURE_V2.md Phase 4):
    pack_cards: walk WordTimings; break on (5-word cap | width > 25*1.9 |
        next-word gap > 0.20s | trailing clause punct with len >= 2).
    group_into_shots: walk Cards; break on (duration > 7.0s | 4-card cap).
    Roles: idx 0 = hook, idx -1 = payoff, else mechanism (1-shot reel = hook).
    veo_duration: smallest of (4, 6, 8) >= duration_s + 1.0s safety.
"""

from __future__ import annotations

from ..models import Card, Shot, WordTiming
from .font_metrics import (
    MAX_CHARS_PER_LINE,
    MAX_LINES_PER_CARD,
    line_count,
    measured_width,
)

# Break thresholds (named for readability and to keep the algo self-documenting).
MAX_WORDS_PER_CARD: int = 5
MAX_WIDTH_PER_CARD: float = MAX_CHARS_PER_LINE * 1.9  # would need 3+ lines
MAX_GAP_S: float = 0.20  # natural pause / breath
CLAUSE_PUNCT: tuple[str, ...] = (",", ".", "!", "?", "—", ";")

MAX_CARDS_PER_SHOT: int = 4
# Soft cap — we PREFER shots ≤ 7.0s (8s Veo bucket with 1s safety) but if a
# single card runs longer (forced-alignment surfaces real internal pauses
# the proportional estimator never saw), we let it through up to the hard
# Veo bucket cap.
MAX_SHOT_DURATION_S: float = 7.0
HARD_SHOT_DURATION_S: float = 8.0   # the actual Veo bucket ceiling
VEO_BUCKETS: tuple[int, ...] = (4, 6, 8)
VEO_SAFETY_S: float = 1.0


def _emit_card(card_words: list[WordTiming]) -> Card:
    """Build a Card from accumulated WordTimings. Assumes non-empty."""
    text = " ".join(w.word for w in card_words)
    return Card(
        text=text,
        words=list(card_words),
        start_s=card_words[0].start_s,
        end_s=card_words[-1].end_s,
        line_count=line_count(text),
    )


def _ends_in_clause_punct(word: str) -> bool:
    """True if the word's trailing char is clause-terminating punctuation."""
    if not word:
        return False
    return word[-1] in CLAUSE_PUNCT


def pack_cards(word_timings: list[WordTiming]) -> list[Card]:
    """Pack TTS word timings into subtitle cards.

    Walks words in order. Starts a new card when empty; otherwise appends and
    checks break conditions (in priority order). When a break fires, emits the
    card and starts the next one. Trailing single-word residue is folded into
    the previous card if possible, else emitted standalone.
    """
    if not word_timings:
        return []

    cards: list[Card] = []
    card_words: list[WordTiming] = []

    for i, w in enumerate(word_timings):
        # Width-cap check BEFORE appending: if the current card already has
        # words AND adding this word would overflow the 1.9-line cap OR push
        # the wrapped layout past MAX_LINES_PER_CARD, emit the in-progress
        # card first and start a fresh one with this word. (Other break
        # conditions keep the word in the current card.)
        if card_words:
            prospective_text = " ".join(cw.word for cw in card_words) + " " + w.word
            if (
                measured_width(prospective_text) > MAX_WIDTH_PER_CARD
                or line_count(prospective_text) > MAX_LINES_PER_CARD
            ):
                cards.append(_emit_card(card_words))
                card_words = []

        card_words.append(w)
        card_text = " ".join(cw.word for cw in card_words)
        is_last = i == len(word_timings) - 1

        # Lookahead gap: end of this word → start of the NEXT word.
        gap_to_next: float | None = None
        if not is_last:
            gap_to_next = word_timings[i + 1].start_s - w.end_s

        # Break conditions in priority order. Earliest match wins.
        # (Width is handled above as a pre-append check.)
        hit_word_cap = len(card_words) >= MAX_WORDS_PER_CARD
        hit_width_cap = measured_width(card_text) > MAX_WIDTH_PER_CARD
        hit_gap = gap_to_next is not None and gap_to_next > MAX_GAP_S
        hit_clause = _ends_in_clause_punct(w.word) and len(card_words) >= 2

        should_break = hit_word_cap or hit_width_cap or hit_gap or hit_clause

        if should_break or is_last:
            cards.append(_emit_card(card_words))
            card_words = []

    # Trailing single-word card: rule 4 prevents clause-breaking on a lone
    # word, so a trailing singleton is almost always the residue of running
    # out of words. Fold into the previous card IF the prev->tail boundary
    # has no audible gap (the gap rule would have intentionally split for a
    # reason — preserve that). Width / word caps act as additional guards.
    if len(cards) >= 2 and len(cards[-1].words) == 1:
        prev = cards[-2]
        tail = cards[-1]
        gap_across = tail.start_s - prev.end_s
        merged_words = prev.words + tail.words
        merged_text = " ".join(cw.word for cw in merged_words)
        if (
            gap_across <= MAX_GAP_S
            and len(merged_words) <= MAX_WORDS_PER_CARD
            and measured_width(merged_text) <= MAX_WIDTH_PER_CARD
            and line_count(merged_text) <= MAX_LINES_PER_CARD
        ):
            cards[-2] = Card(
                text=merged_text,
                words=merged_words,
                start_s=prev.start_s,
                end_s=tail.end_s,
                line_count=line_count(merged_text),
            )
            cards.pop()

    return cards


def _veo_bucket(duration_s: float) -> int:
    """Smallest Veo bucket that holds ``duration_s``.

    Prefers ``duration_s + 1.0s safety``. If that exceeds the largest bucket
    but the duration itself fits the bucket, returns the largest bucket
    (sacrificing the safety margin). Raises only if the duration exceeds
    the largest bucket itself.
    """
    target_with_safety = duration_s + VEO_SAFETY_S
    for d in VEO_BUCKETS:
        if d >= target_with_safety:
            return d
    # Long card (likely a single sentence with internal TTS pauses): use the
    # largest bucket without the safety margin.
    if duration_s <= VEO_BUCKETS[-1]:
        return VEO_BUCKETS[-1]
    raise ValueError(
        f"Shot duration {duration_s:.2f}s exceeds Veo's max bucket "
        f"({VEO_BUCKETS[-1]}s) — TTS audio has an unusually long single "
        f"clause; consider splitting the script."
    )


def _role_for(idx: int, total: int) -> str:
    """hook / mechanism / payoff based on position. 1-shot reel = hook."""
    if total <= 1:
        return "hook"
    if idx == 0:
        return "hook"
    if idx == total - 1:
        return "payoff"
    return "mechanism"


def group_into_shots(
    cards: list[Card],
    roles_hint: list[str] | None = None,
) -> list[Shot]:
    """Group subtitle cards into shots (each = 1 Veo i2v call).

    Walks cards in order. Starts a new shot when empty; otherwise appends and
    checks break conditions. Computes veo_duration as the smallest bucket
    >= duration_s + 1.0s. Assigns role based on shot position.

    `roles_hint` is currently unused — narrative-beat boundaries from the
    script writer's `mechanism_lines` will plug in here later.
    """
    del roles_hint  # reserved for narrative-beat alignment (not yet wired)

    if not cards:
        return []

    # First pass: group cards into shot-card lists.
    shot_card_groups: list[list[Card]] = []
    current: list[Card] = []
    shot_start: float = cards[0].start_s

    for card in cards:
        # Would adding this card push us past the duration cap?
        prospective_end = card.end_s
        prospective_duration = prospective_end - shot_start

        if current and (
            prospective_duration > MAX_SHOT_DURATION_S
            or len(current) >= MAX_CARDS_PER_SHOT
        ):
            shot_card_groups.append(current)
            current = []
            shot_start = card.start_s

        current.append(card)

    if current:
        shot_card_groups.append(current)

    # Second pass: materialize Shot objects with idx / role / veo_duration.
    total = len(shot_card_groups)
    shots: list[Shot] = []
    for idx, group in enumerate(shot_card_groups):
        start_s = group[0].start_s
        end_s = group[-1].end_s
        duration_s = end_s - start_s
        # Guard against zero-duration edge case (single card with start==end).
        if duration_s <= 0:
            duration_s = max(end_s - start_s, 0.01)
        shots.append(
            Shot(
                idx=idx,
                cards=group,
                start_s=start_s,
                end_s=end_s,
                duration_s=duration_s,
                role=_role_for(idx, total),
                veo_duration=_veo_bucket(duration_s),
            )
        )

    return shots
