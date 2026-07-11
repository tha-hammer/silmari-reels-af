"""Reusable conformance suite for the ``HqRecreateGuard`` protocol (Plan 2 → Plan 6).

Plan 2 (`reel_af.recreate`) OWNS the ``HqRecreateGuard`` protocol + semantics but only
ships an in-memory guard for unit tests. Plan 6 backs it with the carousel repo so the
per-carousel HQ-recreate cap survives across HTTP requests (Plan 2 GAP1 / obligation #2).

This module gives Plan 6 a ready-made, implementation-agnostic contract test:

    from test_recreate_guard_contract import run_hq_guard_contract

    def test_pg_guard_conforms(pg_repo):
        run_hq_guard_contract(lambda cap: PgHqRecreateGuard(pg_repo, cap=cap))

It asserts the OBSERVABLE contract every guard must satisfy — fresh count, monotonic
increment, cap boundary (the cap-th succeeds, the (cap+1)-th raises `HqRecreateCapError`),
per-carousel independence, and "count never exceeds cap even under repeated over-cap
attempts". True DB-level atomicity of `register` (check-and-increment under concurrency)
is a repo-impl requirement Plan 6 must prove with its own concurrent-request closure
test — see ``assert_over_cap_never_exceeds`` for the observable invariant this suite can
check in-process, and the note below.
"""

from __future__ import annotations

import pytest

from reel_af.recreate import HqRecreateCapError


class _RefMemGuard:
    """Reference in-memory HqRecreateGuard — the semantics Plan 6 must match."""

    def __init__(self, cap: int):
        self.cap = cap
        self._counts: dict[str, int] = {}

    def register(self, carousel_id: str) -> None:
        n = self._counts.get(carousel_id, 0)
        if n >= self.cap:
            raise HqRecreateCapError(carousel_id, self.cap)
        self._counts[carousel_id] = n + 1

    def count(self, carousel_id: str) -> int:
        return self._counts.get(carousel_id, 0)


def run_hq_guard_contract(make_guard) -> None:
    """Assert the full HqRecreateGuard contract for ``make_guard(cap) -> guard``.

    ``make_guard`` is a factory taking an int cap and returning a fresh guard whose
    per-carousel count starts at 0. Call this from any impl's test module.
    """
    for cap in (0, 1, 3, 5):
        _assert_cap_boundary(make_guard, cap)
    _assert_per_carousel_independent(make_guard)
    _assert_over_cap_never_exceeds(make_guard)


def _assert_cap_boundary(make_guard, cap: int) -> None:
    guard = make_guard(cap)
    assert guard.count("c") == 0  # fresh carousel starts empty
    for i in range(cap):  # the first `cap` registers succeed and increment monotonically
        guard.register("c")
        assert guard.count("c") == i + 1
    with pytest.raises(HqRecreateCapError):  # the (cap+1)-th is rejected
        guard.register("c")
    assert guard.count("c") == cap  # a rejected register did not increment past cap


def _assert_per_carousel_independent(make_guard) -> None:
    guard = make_guard(1)
    guard.register("A")  # A now at its cap
    guard.register("B")  # B keeps its own independent budget
    assert guard.count("A") == 1 and guard.count("B") == 1
    with pytest.raises(HqRecreateCapError):
        guard.register("A")
    assert guard.count("B") == 1  # A's rejection does not touch B


def _assert_over_cap_never_exceeds(make_guard) -> None:
    """Observable invariant behind the atomicity requirement: repeated over-cap
    registers keep raising and never push the count past the cap. (This checks the
    OBSERVABLE contract in-process; Plan 6 must additionally prove DB-level atomic
    check-and-increment with a concurrent-request closure test.)"""
    guard = make_guard(2)
    guard.register("c")
    guard.register("c")
    for _ in range(5):  # every further attempt raises; count is pinned at the cap
        with pytest.raises(HqRecreateCapError):
            guard.register("c")
        assert guard.count("c") == 2


# ── prove the suite itself is valid against the reference in-memory guard ──


def test_reference_mem_guard_conforms():
    run_hq_guard_contract(_RefMemGuard)


def test_cap_zero_rejects_first_register():
    guard = _RefMemGuard(0)
    with pytest.raises(HqRecreateCapError):
        guard.register("c")
    assert guard.count("c") == 0
