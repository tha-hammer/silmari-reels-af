"""B1 + B17 → A1 artifacts compile to a renderable FootageReel; non-target inputs fail closed.

B1 is a CHARACTERIZATION test: it asserts existing primitives already work on
A1-shaped input. Its Red is red because the fixtures are missing, not because
production code is wrong. If it fails after the fixtures land, the failure IS the
finding.

B17 (the CT-1 <-> CT-2 golden parity leg) is BLOCKING and lives in
tests/test_dsl_hooks_worker_closure.py, where the worker can actually be invoked.
"""

from __future__ import annotations

import json

import pytest

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite_file
from reel_af.dsl.models import (
    FOOTAGE_REEL_DSL_VERSION,
    FOOTAGE_REEL_SCHEMA_VERSION,
    SourceRef,
    WordsSidecar,
    validate_renderable,
)

A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"


def test_a1_artifacts_compile_to_renderable_footage_reel(fixture_path):
    doc = read_composite_file(fixture_path("a1_composite.ts.md"))
    words = load_words(fixture_path("source.words.json"))

    result = compile_composite(doc, words, SourceRef(source_url=A1_SOURCE_URL))

    assert result.status != "error", [d.code for d in result.diagnostics]
    assert result.plan is not None
    assert result.plan.schema_version == FOOTAGE_REEL_SCHEMA_VERSION
    assert result.plan.dsl_version == FOOTAGE_REEL_DSL_VERSION
    assert result.plan.source_url == A1_SOURCE_URL
    validate_renderable(result.plan)  # must not raise


def test_a1_words_sidecar_validates_against_reel_af_model(fixture_path):
    raw = fixture_path("source.words.json").read_text(encoding="utf-8")

    sidecar = WordsSidecar.model_validate_json(raw)

    assert sidecar.schema_version == "1"
    assert sidecar.words


def test_a1_hook_plan_fixture_is_v1_shaped(fixture_path):
    plan = json.loads(fixture_path("a1_hook_plan.json").read_text(encoding="utf-8"))

    assert plan["schema_version"] == "1"
    assert plan["workflow"] == "dsl_hooks"
    assert plan["source_url"] == A1_SOURCE_URL
    clip = plan["clips"][0]
    assert plan["duration_bounds_s"]["min"] <= (clip["end_s"] - clip["start_s"])
    assert (clip["end_s"] - clip["start_s"]) <= plan["duration_bounds_s"]["max"]
    assert {c["type"] for c in clip["cut_ins"]} == {"zoom", "visual"}


def test_a1_hook_plan_cutins_carry_evidence_and_windows(fixture_path):
    plan = json.loads(fixture_path("a1_hook_plan.json").read_text(encoding="utf-8"))

    for cut_in in plan["clips"][0]["cut_ins"]:
        assert cut_in["until_s"] > cut_in["at_s"]
        assert cut_in["line"]
        if cut_in["type"] == "visual":
            assert cut_in["image_prompt"]
        else:
            assert cut_in["zoom_focus"]


def test_empty_composite_fails_closed(source_words_sidecar, tmp_path):
    empty = tmp_path / "empty.ts.md"
    empty.write_text("", encoding="utf-8")
    doc = read_composite_file(empty)

    result = compile_composite(doc, source_words_sidecar, SourceRef(source_url=A1_SOURCE_URL))

    assert result.status == "error"
    assert "EMPTY_COMPOSITE" in {d.code for d in result.diagnostics}
    assert result.plan is None


def test_unmatched_segment_fails_closed_no_invented_clip(source_words_sidecar, tmp_path):
    """Text with no source span must STOP, never invent a clip."""
    bogus = tmp_path / "bogus.ts.md"
    bogus.write_text(
        "00:00:04.120  this sentence does not appear anywhere in the source transcript at all\n",
        encoding="utf-8",
    )
    doc = read_composite_file(bogus)

    result = compile_composite(doc, source_words_sidecar, SourceRef(source_url=A1_SOURCE_URL))

    assert result.status == "error"
    assert "UNMATCHED_SEGMENT" in {d.code for d in result.diagnostics}
    assert result.plan is None


@pytest.mark.parametrize("bad_version", ["0", "2", "99"])
def test_words_sidecar_rejects_foreign_schema_version(fixture_path, bad_version):
    data = json.loads(fixture_path("source.words.json").read_text(encoding="utf-8"))
    data["schema_version"] = bad_version

    with pytest.raises(ValueError):
        WordsSidecar.model_validate(data)
