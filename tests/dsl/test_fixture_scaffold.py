from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_v1_supported_fixture_is_v1_supported(read_fixture):
    text = read_fixture("v1_supported.ts.md")

    assert "[insert black 2.5]" in text
    assert "[insert relevant 25]" not in text
    assert sum(1 for line in text.splitlines() if line[:2].isdigit()) == 4


def test_source_words_fixture_validates_with_real_model(source_words_sidecar):
    sidecar = source_words_sidecar

    assert sidecar.schema_version == "1"
    assert sidecar.words
    assert sidecar.segments
    assert all(word.start <= word.end for word in sidecar.words)


@given(st.sampled_from(["v1_supported.ts.md", "source.words.json"]))
def test_fixture_path_points_at_checked_in_fixtures(fixture_name):
    path = FIXTURES_DIR / fixture_name

    assert path.exists()
    assert path.is_file()


def test_lavfi_factory_generates_real_mp4(lavfi_mp4_factory):
    path = lavfi_mp4_factory(name="fixture-source", duration_s=0.5)

    assert path.exists()
    assert path.suffix == ".mp4"
    assert path.stat().st_size > 0


def test_source_words_fixture_matches_file(source_words_sidecar, fixture_path):
    payload = json.loads(fixture_path("source.words.json").read_text())

    assert source_words_sidecar.model_dump() == payload
