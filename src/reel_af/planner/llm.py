"""LLM port for planner candidate mining, strategy, and arrangement."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from baml_client.types import (
    CandidateSpan,
    DurationBounds,
    PlannerCandidate,
    ReelBlueprint,
    ReelStrategy,
)
from reel_af.planner.config import load_planner_config

Register = Literal["entertainment", "educational", "b2b"]


class BamlPlannerError(RuntimeError):
    """Base error for deterministic BAML adapter failures."""


class BamlPlannerInputError(BamlPlannerError, ValueError):
    """The caller supplied an invalid local planner input."""


class BamlPlannerContractError(BamlPlannerError, ValueError):
    """The BAML response violated a local planner contract."""


def _baml_functions() -> Any:
    from baml_client.async_client import b

    return b


def _client_registry_type() -> type[Any]:
    from baml_py import ClientRegistry

    return ClientRegistry


def _client_registry(cfg: Any) -> Any:
    registry = _client_registry_type()()
    registry.add_llm_client(
        name="planner_runtime",
        provider="openrouter",
        options=_client_options(cfg),
    )
    registry.set_primary("planner_runtime")
    return registry


def _client_options(cfg: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "model": _cfg_value(cfg, "model", default="anthropic/claude-sonnet-5"),
        "api_key": _cfg_value(cfg, "api_key", "openrouter_api_key")
        or os.environ.get("OPENROUTER_API_KEY", ""),
        "temperature": float(_cfg_value(cfg, "llm_temperature", "temperature", default=0.4)),
    }
    for key in (
        "llm_connect_timeout_s",
        "llm_request_timeout_s",
        "llm_total_timeout_s",
    ):
        value = _cfg_value(cfg, key)
        if value is not None:
            options[key] = float(value)
    return options


def _cfg_value(cfg: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(cfg, Mapping) and name in cfg:
            return cfg[name]
        if hasattr(cfg, name):
            return getattr(cfg, name)
    return default


def _require_bounds(bounds: DurationBounds | None) -> DurationBounds:
    if bounds is None:
        raise BamlPlannerInputError("duration bounds are required for BAML strategize")
    min_s, max_s = _bounds_values(bounds)
    if min_s <= 0 or max_s <= 0 or max_s < min_s:
        raise BamlPlannerInputError("duration bounds must be positive and ordered")
    return bounds


def _bounds_values(bounds: DurationBounds) -> tuple[float, float]:
    return float(_field(bounds, "min_s")), float(_field(bounds, "max_s"))


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value[name]
    return getattr(value, name)


def _enforce_text_limit(cfg: Any, transcript: str) -> None:
    limit = _cfg_value(cfg, "max_transcript_chars")
    if limit is not None and len(transcript) > int(limit):
        raise BamlPlannerInputError("transcript exceeds max_transcript_chars")


def _enforce_candidate_limit(cfg: Any, candidates: list[Any]) -> None:
    limit = _cfg_value(cfg, "max_candidates")
    if limit is not None and len(candidates) > int(limit):
        raise BamlPlannerContractError("BAML returned more than max_candidates")


class PlannerLLM(Protocol):
    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        """Return candidate transcript spans for a register."""

    async def strategize(
        self,
        transcript: str,
        candidates: list[PlannerCandidate],
        bounds: DurationBounds,
    ) -> ReelStrategy:
        """Return a reel strategy for candidate spans."""

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        *,
        repair_hint: str | None = None,
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
        candidates: list[PlannerCandidate],
        bounds: DurationBounds,
    ) -> ReelStrategy:
        self.calls.append(("strategize", bounds))
        return self.strategy

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        *,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        self.calls.append(("arrange", repair_hint))
        return self.blueprint


class NeverPlannerLLM:
    """PlannerLLM test double for paths that must not touch the LLM."""

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        raise AssertionError("PlannerLLM.mine must not be called")

    async def strategize(
        self,
        transcript: str,
        candidates: list[PlannerCandidate],
        bounds: DurationBounds,
    ) -> ReelStrategy:
        raise AssertionError("PlannerLLM.strategize must not be called")

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        *,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        raise AssertionError("PlannerLLM.arrange must not be called")


class BamlPlannerLLM:
    """Runtime BAML adapter.

    The generated ``baml_client`` surface is imported lazily so unit tests and
    deterministic planner layers do not require generated BAML code.
    """

    def __init__(self, *, cfg: Any | None = None) -> None:
        self.cfg = cfg or load_planner_config()
        self._registry: Any | None = None

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        _enforce_text_limit(self.cfg, transcript)
        result = await _baml_functions().MineCandidates(
            transcript,
            register,
            baml_options=self._baml_options(),
        )
        raw_candidates = list(result)
        _enforce_candidate_limit(self.cfg, raw_candidates)
        return raw_candidates

    async def strategize(
        self,
        transcript: str,
        candidates: list[PlannerCandidate],
        bounds: DurationBounds,
    ) -> ReelStrategy:
        bounds = _require_bounds(bounds)
        result = await _baml_functions().StrategizeReel(
            transcript,
            candidates,
            bounds,
            baml_options=self._baml_options(),
        )
        min_s, max_s = _bounds_values(bounds)
        if not min_s <= result.target_duration_s <= max_s:
            raise BamlPlannerContractError("strategy target_duration_s is outside duration bounds")
        return result

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy | None,
        *,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        if strategy is None:
            raise BamlPlannerInputError("strategy is required for BAML arrange")
        result = await _baml_functions().ArrangeReel(
            candidates,
            strategy,
            repair_hint,
            baml_options=self._baml_options(),
        )
        return result

    def _baml_options(self) -> dict[str, Any]:
        if self._registry is None:
            self._registry = _client_registry(self.cfg)
        return {"client_registry": self._registry}
