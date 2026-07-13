"""B6 (consumer half) + B3 contract: the research.completed CloudEvents envelope +
small DTO shape (golden fixture) and the by-reference result-snapshot reader.

Pins two shapes to shared golden fixtures so a producer that copies the body into
`data`, or a control-plane change that drops `metadata.query`/`research_package`,
fails loudly here. Also proves reel-af reads `result` only, NEVER `notes`
(owner-scoped; a non-owner read returns `execution_ownership_mismatch` — ANTI A2).
"""

from __future__ import annotations

import json
import os

from control_plane import fetch_document_by_ref  # noqa: E402  (web/ on path via conftest)

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# The small DTO carries exactly these keys — ids + primitives + a small snapshot,
# NEVER the mutable document body (C-Notification).
_EXPECTED_DTO_KEYS = {
    "run_id",
    "status",
    "title",
    "result_ref",
    "research_prompt",
    "research_document_id",
}


def _load(name: str) -> dict:
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class _AccessSpy(dict):
    """Top-level execution mapping that records every key accessed, so a test can
    assert `notes` was never read by the by-reference reader (ANTI A2)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accessed: set = set()

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)


# ─────────────────────────── B6: envelope + small DTO ───────────────────────────


def test_envelope_type_and_subject_is_execution_id():
    event = _load("research_completed.cloudevent.json")
    snapshot = _load("execution_result.snapshot.json")
    assert event["type"] == "com.silmari.research.completed.v1"
    assert event["id"]  # present, non-empty
    assert event["subject"] == snapshot["execution_id"]          # C-Correlation
    assert event["data"]["research_document_id"] == snapshot["execution_id"]


def test_data_is_small_dto_with_exact_keys():
    event = _load("research_completed.cloudevent.json")
    assert set(event["data"].keys()) == _EXPECTED_DTO_KEYS


def test_research_package_body_absent_from_data():
    event = _load("research_completed.cloudevent.json")
    assert "research_package" not in event["data"]               # C-Notification


# ─────────────────────────── B3: by-reference reader ───────────────────────────


def test_fetch_maps_package_prompt_and_document_id():
    snapshot = _load("execution_result.snapshot.json")
    doc = fetch_document_by_ref(snapshot)
    assert doc.research_package == snapshot["result"]["research_package"]
    assert doc.research_prompt == snapshot["result"]["metadata"]["query"]
    assert doc.document_id == snapshot["execution_id"]
    assert doc.package_present is True


def test_fetch_never_reads_notes():
    snapshot = _AccessSpy(_load("execution_result.snapshot.json"))
    fetch_document_by_ref(snapshot)
    assert "notes" not in snapshot.accessed                      # ANTI A2


def test_prompt_falls_back_to_dto_when_metadata_query_missing():
    snapshot = _load("execution_result.snapshot.json")
    snapshot["result"]["metadata"].pop("query")
    doc = fetch_document_by_ref(snapshot, dto_prompt="fallback prompt")
    assert doc.research_prompt == "fallback prompt"              # ISC-10


def test_prompt_is_none_when_both_missing():
    snapshot = _load("execution_result.snapshot.json")
    snapshot["result"]["metadata"].pop("query")
    doc = fetch_document_by_ref(snapshot)
    assert doc.research_prompt is None


def test_missing_research_package_flagged_not_silent_empty():
    snapshot = _load("execution_result.snapshot.json")
    snapshot["result"].pop("research_package")
    doc = fetch_document_by_ref(snapshot)
    assert doc.research_package is None
    assert doc.package_present is False                          # ISC-9 malformed OUTPUT
