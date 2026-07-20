"""LLM port for planner candidate mining, strategy, and arrangement."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from baml_client.types import (
    CandidateSpan,
    CandidateTranscriptContext,
    DurationPolicy,
    PlannerCandidate,
    ReelBlueprint,
    ReelStrategy,
    ScriptBeatText,
    ScriptCoherenceFixAction,
    ScriptCoherenceReport,
    ScriptTransition,
    ScriptTransitionReview,
    ScriptTransitionVerdict,
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


def _require_duration_policy(policy: DurationPolicy | None) -> DurationPolicy:
    if policy is None:
        raise BamlPlannerInputError("duration policy is required for BAML strategize")
    soft_cap_s = float(_field(policy, "soft_cap_s"))
    effective_cap_s = float(_field(policy, "effective_cap_s"))
    if soft_cap_s <= 0 or effective_cap_s <= 0:
        raise BamlPlannerInputError("duration policy caps must be positive")
    if effective_cap_s < soft_cap_s and not bool(_field(policy, "cap_overridden")):
        raise BamlPlannerInputError("duration policy effective_cap_s must not shrink the soft cap")
    return policy


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value[name]
    return getattr(value, name)


def _optional_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _require_rationale(value: Any, phase: str) -> None:
    if not str(_optional_field(value, "rationale", "") or "").strip():
        raise BamlPlannerContractError(f"{phase} rationale is required")


def _require_completion_rationale(value: Any) -> None:
    if not str(_optional_field(value, "completion_rationale", "") or "").strip():
        raise BamlPlannerContractError("ArrangeReel completion_rationale is required")


def _require_beat_rationales(value: Any) -> None:
    for index, beat in enumerate(_optional_field(value, "beats", None) or []):
        if str(_optional_field(beat, "rationale", "") or "").strip():
            continue
        candidate_id = _optional_field(beat, "candidate_id", f"beat[{index}]")
        raise BamlPlannerContractError(
            f"ArrangeReel beat rationale is required for {candidate_id}"
        )


def _require_script_coherence_report(
    report: ScriptCoherenceReport,
    expected_transition_count: int,
) -> None:
    if not str(_optional_field(report, "overall_rationale", "") or "").strip():
        raise BamlPlannerContractError("CheckScriptCoherence overall_rationale is required")
    transitions = list(_optional_field(report, "transitions", None) or [])
    if len(transitions) != expected_transition_count:
        raise BamlPlannerContractError(
            "CheckScriptCoherence transition count must match input transitions"
        )
    for index, transition in enumerate(transitions):
        if not str(_optional_field(transition, "rationale", "") or "").strip():
            raise BamlPlannerContractError(
                f"CheckScriptCoherence transition rationale is required for transition {index}"
            )
        verdict = _optional_field(transition, "verdict", None)
        fix_action = _optional_field(transition, "fix_action", None)
        if verdict is None or fix_action is None:
            raise BamlPlannerContractError(
                f"CheckScriptCoherence verdict and fix_action are required for transition {index}"
            )


def _duration_range_bounds(value: Any, phase: str) -> tuple[float, float]:
    duration_range = _optional_field(value, "duration_range_s", None)
    if duration_range is None:
        raise BamlPlannerContractError(f"{phase} duration_range_s is required")
    min_s = float(_field(duration_range, "min_s"))
    max_s = float(_field(duration_range, "max_s"))
    rationale = str(_optional_field(duration_range, "rationale", "") or "").strip()
    if min_s <= 0 or max_s <= 0 or max_s < min_s:
        raise BamlPlannerContractError(f"{phase} duration_range_s must be positive and ordered")
    if not rationale:
        raise BamlPlannerContractError(f"{phase} duration_range_s rationale is required")
    return min_s, max_s


def _require_duration_range(value: Any, phase: str) -> None:
    _duration_range_bounds(value, phase)


def _require_duration_range_under_policy(value: Any, policy: DurationPolicy, phase: str) -> None:
    _min_s, max_s = _duration_range_bounds(value, phase)
    effective_cap_s = float(_field(policy, "effective_cap_s"))
    if max_s > effective_cap_s:
        raise BamlPlannerContractError(
            f"{phase} duration_range_s max_s exceeds duration_policy effective_cap_s"
        )


def _require_arc(value: Any, candidates: list[PlannerCandidate], phase: str) -> None:
    arc = _optional_field(value, "arc", None)
    if arc is None:
        raise BamlPlannerContractError(f"{phase} arc is required")
    criteria = [
        str(item).strip()
        for item in (_optional_field(arc, "completion_criteria", None) or [])
        if str(item).strip()
    ]
    required_ids = [
        str(item).strip()
        for item in (_optional_field(arc, "required_candidate_ids", None) or [])
        if str(item).strip()
    ]
    if not str(_optional_field(arc, "promise", "") or "").strip():
        raise BamlPlannerContractError(f"{phase} arc promise is required")
    if not str(_optional_field(arc, "thread", "") or "").strip():
        raise BamlPlannerContractError(f"{phase} arc thread is required")
    if not criteria:
        raise BamlPlannerContractError(f"{phase} completion_criteria are required")
    if not required_ids:
        raise BamlPlannerContractError(f"{phase} required_candidate_ids are required")
    known_ids = {str(_field(candidate, "candidate_id")) for candidate in candidates}
    unknown = sorted(set(required_ids) - known_ids)
    if unknown:
        raise BamlPlannerContractError(
            f"{phase} required_candidate_ids are unknown: {', '.join(unknown)}"
        )


def _require_strategy_contract(
    strategy: ReelStrategy,
    candidates: list[PlannerCandidate],
    duration_policy: DurationPolicy,
) -> None:
    _require_duration_range_under_policy(strategy, duration_policy, "StrategizeReel")
    _require_arc(strategy, candidates, "StrategizeReel")
    _require_rationale(strategy, "StrategizeReel")


def _require_candidate_rationales(candidates: list[Any]) -> None:
    for index, candidate in enumerate(candidates, start=1):
        if str(_optional_field(candidate, "rationale", "") or "").strip():
            continue
        candidate_id = _optional_field(candidate, "candidate_id", f"candidate[{index}]")
        raise BamlPlannerContractError(f"MineCandidates rationale is required for {candidate_id}")


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
        duration_policy: DurationPolicy,
    ) -> ReelStrategy:
        """Return a reel strategy for candidate spans."""

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        *,
        candidate_contexts: list[CandidateTranscriptContext] | None = None,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        """Return the final typed reel blueprint."""

    async def check_script_coherence(
        self,
        blueprint: ReelBlueprint,
        script_beats: list[ScriptBeatText],
        transitions: list[ScriptTransition],
        strategy: ReelStrategy,
        candidate_contexts: list[CandidateTranscriptContext],
        *,
        repair_hint: str | None = None,
    ) -> ScriptCoherenceReport:
        """Return a transition-level coherence report for resolved script text."""


class FakePlannerLLM:
    """Canned PlannerLLM used by deterministic producer tests."""

    def __init__(
        self,
        *,
        candidates: list[CandidateSpan],
        strategy: ReelStrategy,
        blueprint: ReelBlueprint,
        coherence: ScriptCoherenceReport | None = None,
    ) -> None:
        self.candidates = candidates
        self.strategy = strategy
        self.blueprint = blueprint
        self.coherence = coherence
        self.calls: list[tuple[Any, ...]] = []

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        self.calls.append(("mine", register))
        return self.candidates

    async def strategize(
        self,
        transcript: str,
        candidates: list[PlannerCandidate],
        duration_policy: DurationPolicy,
    ) -> ReelStrategy:
        self.calls.append(("strategize", duration_policy))
        return self.strategy

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        *,
        candidate_contexts: list[CandidateTranscriptContext] | None = None,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        self.calls.append(("arrange", repair_hint))
        return self.blueprint

    async def check_script_coherence(
        self,
        blueprint: ReelBlueprint,
        script_beats: list[ScriptBeatText],
        transitions: list[ScriptTransition],
        strategy: ReelStrategy,
        candidate_contexts: list[CandidateTranscriptContext],
        *,
        repair_hint: str | None = None,
    ) -> ScriptCoherenceReport:
        self.calls.append(("check_script_coherence", repair_hint))
        return self.coherence or _coherent_report(transitions)


class NeverPlannerLLM:
    """PlannerLLM test double for paths that must not touch the LLM."""

    async def mine(self, transcript: str, register: Register) -> list[CandidateSpan]:
        raise AssertionError("PlannerLLM.mine must not be called")

    async def strategize(
        self,
        transcript: str,
        candidates: list[PlannerCandidate],
        duration_policy: DurationPolicy,
    ) -> ReelStrategy:
        raise AssertionError("PlannerLLM.strategize must not be called")

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy,
        *,
        candidate_contexts: list[CandidateTranscriptContext] | None = None,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        raise AssertionError("PlannerLLM.arrange must not be called")

    async def check_script_coherence(
        self,
        blueprint: ReelBlueprint,
        script_beats: list[ScriptBeatText],
        transitions: list[ScriptTransition],
        strategy: ReelStrategy,
        candidate_contexts: list[CandidateTranscriptContext],
        *,
        repair_hint: str | None = None,
    ) -> ScriptCoherenceReport:
        raise AssertionError("PlannerLLM.check_script_coherence must not be called")


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
        _require_candidate_rationales(raw_candidates)
        return raw_candidates

    async def strategize(
        self,
        transcript: str,
        candidates: list[PlannerCandidate],
        duration_policy: DurationPolicy,
    ) -> ReelStrategy:
        duration_policy = _require_duration_policy(duration_policy)
        result = await _baml_functions().StrategizeReel(
            transcript,
            candidates,
            duration_policy,
            baml_options=self._baml_options(),
        )
        _require_strategy_contract(result, candidates, duration_policy)
        return result

    async def arrange(
        self,
        candidates: list[PlannerCandidate],
        strategy: ReelStrategy | None,
        *,
        candidate_contexts: list[CandidateTranscriptContext] | None = None,
        repair_hint: str | None = None,
    ) -> ReelBlueprint:
        if strategy is None:
            raise BamlPlannerInputError("strategy is required for BAML arrange")
        result = await _baml_functions().ArrangeReel(
            candidates,
            strategy,
            candidate_contexts or [],
            repair_hint,
            baml_options=self._baml_options(),
        )
        _require_duration_range_under_policy(
            result,
            _field(strategy, "duration_policy"),
            "ArrangeReel",
        )
        _require_arc(result, candidates, "ArrangeReel")
        _require_completion_rationale(result)
        _require_beat_rationales(result)
        _require_rationale(result, "ArrangeReel")
        return result

    async def check_script_coherence(
        self,
        blueprint: ReelBlueprint,
        script_beats: list[ScriptBeatText],
        transitions: list[ScriptTransition],
        strategy: ReelStrategy,
        candidate_contexts: list[CandidateTranscriptContext],
        *,
        repair_hint: str | None = None,
    ) -> ScriptCoherenceReport:
        result = await _baml_functions().CheckScriptCoherence(
            blueprint,
            script_beats,
            transitions,
            strategy,
            candidate_contexts,
            repair_hint,
            baml_options=self._baml_options(),
        )
        _require_script_coherence_report(result, len(transitions))
        return result

    def _baml_options(self) -> dict[str, Any]:
        if self._registry is None:
            self._registry = _client_registry(self.cfg)
        return {"client_registry": self._registry}


def _coherent_report(transitions: list[ScriptTransition]) -> ScriptCoherenceReport:
    return ScriptCoherenceReport(
        coherent=True,
        transitions=[
            ScriptTransitionReview(
                transition_index=int(_optional_field(transition, "index", index)),
                from_beat_index=int(_optional_field(transition, "from_beat_index", index)),
                to_beat_index=int(_optional_field(transition, "to_beat_index", index + 1)),
                verdict=ScriptTransitionVerdict.Coherent,
                fix_action=ScriptCoherenceFixAction.Keep,
                why_present=True,
                rationale="the transition reads coherently in the assembled script",
                missing_why=None,
                suggested_bridge_candidate_ids=[],
                suggested_repair=None,
            )
            for index, transition in enumerate(transitions)
        ],
        overall_rationale="the assembled script keeps one local proof thread",
        repair_hint=None,
    )
