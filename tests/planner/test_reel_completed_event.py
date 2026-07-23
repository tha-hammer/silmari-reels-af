"""AF-89l / B24 — DSL reel-completion payload satisfies the FROZEN
``com.silmari.reel.completed/v1`` contract.

The CP Go emitter (``control-plane/internal/events/reel_completed.go``) reads
``source_execution_id`` / ``duration_s`` / ``beat_count`` from the succeeded
execution's ResultPayload, degrading missing fields to zero-values. So the
``dsl_hooks_to_reels`` return dict must carry ``beat_count`` (=len(segments),
alias of segment_count until v2 — AF-z92) and a populated
``source_execution_id``.

Closure: "the completion payload satisfies the frozen v1 schema."
- SOURCE (seed only): the A1 fixture triple (4-segment compile) driven through
  the worker with a fast-patched render span.
- TRIGGER: the payload builder over the dict dsl_hooks_to_reels returns.
- OBSERVABLE: jsonschema validation against the REAL frozen schema file
  (six required, additionalProperties:false).
- RED-AT-SEAM: dropping beat_count/source_execution_id fails ``required``.
- EXECUTION: plain pytest; FAILS if the schema file is absent (never skips
  green).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest

from reel_af import app as app_mod
from reel_af.app import _reel_completed_payload, dsl_hooks_to_reels

FIXTURES = Path(__file__).resolve().parent.parent / "dsl" / "fixtures"
A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"
FIXTURE_IDEMPOTENCY_KEY = "a1:20260715T093000Z-abc123-7f3a9c:clip:1"

_SCHEMA_CANDIDATES = (
    # Installed (git-locked) agentfield SDK, when it ships contracts.
    Path(__import__("agentfield").__file__).parent
    / "handoff" / "contracts" / "com.silmari.reel.completed" / "v1.schema.json",
    # Sibling agentfield source tree (the contract's home today).
    Path("/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/sdk/python/"
         "agentfield/handoff/contracts/com.silmari.reel.completed/v1.schema.json"),
)


def _frozen_schema() -> dict:
    """Load the REAL frozen contract; a BLOCKING closure never skips green."""
    for candidate in _SCHEMA_CANDIDATES:
        if candidate.exists():
            return json.loads(candidate.read_text())
    pytest.fail(
        "frozen com.silmari.reel.completed/v1 schema not found in: "
        + ", ".join(str(c) for c in _SCHEMA_CANDIDATES)
    )


# ── Harness: fast-patched render span (the payload seam stays real) ──


class _FakeTextProvider:
    def ai(self, system=None, user=None, schema=None, **kw):
        if schema is not None:
            return {"hook": "THEY DON'T REASON", "moments": []}
        return "THEY DON'T REASON"


def _patch_fast_render(monkeypatch):
    def fake_download_segments(plan, out_dir, fetch_segment):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {}

    async def fake_apply_overlays(reel, segment_assets, overlay_plan, out_dir,
                                  run_id, *, image_provider, concurrency=None):
        return segment_assets

    async def fake_stitch(plan, assets, out_dir, *, run_id):
        path = Path(out_dir) / "base.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"base")
        return path

    async def fake_finish(base, ctx, cfg, *, out_dir, raw):
        path = Path(out_dir) / "final.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"final")
        return path

    monkeypatch.setattr(app_mod, "download_segments", fake_download_segments)
    monkeypatch.setattr(app_mod, "apply_overlays", fake_apply_overlays)
    monkeypatch.setattr(app_mod, "stitch_footage_reel", fake_stitch)
    monkeypatch.setattr(app_mod, "finish_reel", fake_finish)


def _run_worker(monkeypatch, tmp_path: Path) -> dict:
    _patch_fast_render(monkeypatch)
    result = asyncio.run(
        dsl_hooks_to_reels(
            source_url=A1_SOURCE_URL,
            composite_ref=str(FIXTURES / "a1_composite.ts.md"),
            words_ref=str(FIXTURES / "source.words.json"),
            hook_ref=str(FIXTURES / "a1_hook_plan.json"),
            clip_idx=1,
            out_dir=str(tmp_path),
            fetch_segment=lambda req: tmp_path / "unused.mp4",
            uploader=lambda p, *, run_id, filename=None: f"https://b.example/{run_id}/reel.mp4",
            text_provider=_FakeTextProvider(),
            image_provider=object(),  # never used: render span is faked
        )
    )
    assert "error" not in result, f"worker failed: {result}"
    return result


# ── Closure (BLOCKING): payload validates against the frozen schema ──


def test_dsl_completion_payload_is_schema_valid(monkeypatch, tmp_path):
    result = _run_worker(monkeypatch, tmp_path)
    payload = _reel_completed_payload(result)

    assert payload["beat_count"] == result["segment_count"] == 4
    assert payload["source_execution_id"] == FIXTURE_IDEMPOTENCY_KEY
    assert payload["source_execution_id"]  # populated, never the CP zero-degrade ""
    jsonschema.validate(payload, _frozen_schema())


# ── RED-AT-SEAM: required fields actually enforced by the schema ─────


@pytest.mark.parametrize("dropped", ["beat_count", "source_execution_id"])
def test_dropping_required_field_fails_schema(monkeypatch, tmp_path, dropped):
    result = _run_worker(monkeypatch, tmp_path)
    payload = _reel_completed_payload(result)
    payload.pop(dropped)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, _frozen_schema())


# ── The worker result itself carries what the CP emitter reads ───────


def test_worker_result_carries_beat_count_and_source_execution_id(monkeypatch, tmp_path):
    result = _run_worker(monkeypatch, tmp_path)
    # beat_count aliases the SEGMENT count (v1 contract), not the cut-in count.
    assert result["beat_count"] == result["segment_count"] == 4
    assert result["source_execution_id"] == FIXTURE_IDEMPOTENCY_KEY


# ── additionalProperties guard: exactly the six contract fields ──────


def test_payload_is_exactly_the_six_contract_fields(monkeypatch, tmp_path):
    result = _run_worker(monkeypatch, tmp_path)
    payload = _reel_completed_payload(result)
    assert set(payload) == {
        "run_id", "status", "reel_ref", "source_execution_id",
        "duration_s", "beat_count",
    }
    assert payload["status"] == "succeeded"
    assert payload["reel_ref"] == result["download_url"]
    assert payload["run_id"] == result["run_id"]
    assert payload["duration_s"] == result["duration_s"]
