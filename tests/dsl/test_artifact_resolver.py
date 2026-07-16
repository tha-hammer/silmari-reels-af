"""Artifact-ref resolver: the reel-af (Railway) worker reading A1 artifacts.

reel-af runs remotely, so A1 refs must resolve by network fetch (http/https) or a
co-located a1://→$A1_ARTIFACTS_BASE map; bare local paths are for tests/fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.app import (
    A1_ARTIFACTS_BASE_ENV,
    _resolve_artifact_ref,
)


def test_local_path_passthrough(tmp_path):
    """A bare local path resolves to itself (fixtures/tests; no fetch)."""
    src = tmp_path / "composite.ts.md"
    src.write_text("body", encoding="utf-8")

    def _no_fetch(url):  # must not be called
        raise AssertionError("fetch called for a local path")

    out = _resolve_artifact_ref(str(src), tmp_path / "work", "composite.ts.md", _no_fetch)
    assert out == Path(str(src))


def test_https_ref_is_fetched_to_dest(tmp_path):
    """An http(s) ref (A1-served or presigned bucket URL) is fetched into dest_dir."""
    dest = tmp_path / "work"
    dest.mkdir()
    seen = {}

    def _fetch(url):
        seen["url"] = url
        return b'{"schema_version":"1","words":[]}'

    out = _resolve_artifact_ref(
        "https://s3.example/bkt/outputs/run/words.json?X-Amz-Expires=1",
        dest,
        "words.json",
        _fetch,
    )
    assert out == dest / "words.json"
    assert out.read_bytes() == b'{"schema_version":"1","words":[]}'
    assert seen["url"].startswith("https://s3.example/")


def test_a1_scheme_maps_to_base_dir(tmp_path, monkeypatch):
    """a1://<rel> maps under $A1_ARTIFACTS_BASE for co-located dev."""
    base = tmp_path / "a1-runs"
    (base / "runs/abc").mkdir(parents=True)
    (base / "runs/abc/hook-plan.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(A1_ARTIFACTS_BASE_ENV, str(base))

    out = _resolve_artifact_ref(
        "a1://runs/abc/hook-plan.json", tmp_path / "work", "hook-plan.json", None
    )
    assert out == base / "runs/abc/hook-plan.json"


def test_a1_scheme_without_base_is_terminal(tmp_path, monkeypatch):
    """a1:// with no base configured fails closed (→ dsl_artifact_unavailable)."""
    monkeypatch.delenv(A1_ARTIFACTS_BASE_ENV, raising=False)
    with pytest.raises(ValueError):
        _resolve_artifact_ref("a1://runs/abc/x.json", tmp_path, "x.json", None)


def test_fetch_failure_propagates_as_oserror(tmp_path):
    """A failed remote fetch surfaces as OSError so the worker maps it to
    dsl_artifact_unavailable rather than crashing."""
    dest = tmp_path / "work"
    dest.mkdir()

    def _boom(url):
        raise OSError("network down")

    with pytest.raises(OSError):
        _resolve_artifact_ref("https://a1.example/composite.ts.md", dest, "c.ts.md", _boom)
