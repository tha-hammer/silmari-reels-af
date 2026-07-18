"""LLM port for planner candidate mining, strategy, and arrangement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from reel_af.planner.models import CandidateSpan, ReelBlueprint, ReelStrategy, Register


class PlannerLLM(Protocol):
    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        """Return candidate transcript spans for a register."""

    async def strategize(
        self,
        transcript: str,
        candidates: list[CandidateSpan],
        bounds: Mapping[str, float] | None,
    ) -> ReelStrategy:
        """Return a reel strategy for candidate spans."""

    async def arrange(
        self,
        candidates: list[CandidateSpan],
        strategy: ReelStrategy | None,
    ) -> ReelBlueprint:
        """Return the final typed reel blueprint."""


class FakePlannerLLM:
    """Canned PlannerLLM used by deterministic producer tests."""

    def __init__(
        self,
        *,
        candidates: list[CandidateSpan],
        strategy: ReelStrategy,
        blueprint: ReelBlueprint,
    ) -> None:
        self.candidates = candidates
        self.strategy = strategy
        self.blueprint = blueprint
        self.calls: list[tuple[Any, ...]] = []

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        self.calls.append(("mine", register))
        return self.candidates

    async def strategize(
        self,
        transcript: str,
        candidates: list[CandidateSpan],
        bounds: Mapping[str, float] | None,
    ) -> ReelStrategy:
        self.calls.append(("strategize", dict(bounds or {})))
        return self.strategy

    async def arrange(
        self,
        candidates: list[CandidateSpan],
        strategy: ReelStrategy | None,
    ) -> ReelBlueprint:
        self.calls.append(("arrange",))
        return self.blueprint


class NeverPlannerLLM:
    """PlannerLLM test double for paths that must not touch the LLM."""

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        raise AssertionError("PlannerLLM.mine must not be called")

    async def strategize(
        self,
        transcript: str,
        candidates: list[CandidateSpan],
        bounds: Mapping[str, float] | None,
    ) -> ReelStrategy:
        raise AssertionError("PlannerLLM.strategize must not be called")

    async def arrange(
        self,
        candidates: list[CandidateSpan],
        strategy: ReelStrategy | None,
    ) -> ReelBlueprint:
        raise AssertionError("PlannerLLM.arrange must not be called")


class BamlPlannerLLM:
    """Runtime BAML adapter.

    The generated ``baml_client`` surface is imported lazily so unit tests and
    deterministic planner layers do not require generated BAML code.
    """

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        from baml_client.async_client import b

        result = await b.MineCandidates(transcript, register)
        return [CandidateSpan.model_validate(item) for item in result]

    async def strategize(
        self,
        transcript: str,
        candidates: list[CandidateSpan],
        bounds: Mapping[str, float] | None,
    ) -> ReelStrategy:
        from baml_client.async_client import b

        result = await b.StrategizeReel(
            transcript,
            [candidate.model_dump() for candidate in candidates],
            dict(bounds or {}),
        )
        return ReelStrategy.model_validate(result)

    async def arrange(
        self,
        candidates: list[CandidateSpan],
        strategy: ReelStrategy | None,
    ) -> ReelBlueprint:
        from baml_client.async_client import b

        result = await b.ArrangeReel(
            [candidate.model_dump() for candidate in candidates],
            strategy.model_dump() if strategy is not None else None,
        )
        return ReelBlueprint.model_validate(result)
