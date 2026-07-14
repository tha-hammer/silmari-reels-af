"""Closure test (BLOCKING) — cross-app reel delivery reaches a downloadable result_ref.

Behavior: a cross-app dispatched reel's `result_ref` is a downloadable bucket URL.

Drives the production reasoner `reel_af.app.research_to_reel` (hermetic injected seams,
including the `uploader` bucket seam) and OBSERVES through the production read path
`web/server._resolve_result_ref` — the exact mapping the web poll (`_handle_poll`) applies to
the CP execution body. The span crosses the reasoner→web-resolver module boundary; the test
seeds/mocks none of it and asserts only through `_resolve_result_ref`.

RED-AT-SEAM: with the uploader disabled (bucket unset → None), no `download_url` is produced,
so `_resolve_result_ref` returns the non-fetchable `cp-execution://…` placeholder — proving the
belt is red without delivery. Fully hermetic: no real S3 / DB / CP.
"""

from __future__ import annotations

import types

from server import _resolve_result_ref  # production read (web/ on path via tests/web/conftest)

_EXEC_ID = "exec_x"


def _fetch_ok(_execution_id):
    # Inline paragraph text is present, so the research_package is not consulted.
    return types.SimpleNamespace(
        execution_id=_EXEC_ID,
        status="succeeded",
        run_id="run_x",
        result={"research_package": {"sections": []}},
    )


def _distiller(_text):
    # A plain dict (no .model_dump) is used as the essence as-is by the reasoner.
    return {"core_claim": "c", "mechanism": "m", "evidence": ["e"],
            "content_mode": "general", "domain": "tech"}


def _composer(_node, _essence):
    return {"hook": "h", "hook_variant": "a", "beats": []}


def _make_renderer(tmp_path):
    async def _renderer(**kwargs):
        return {
            "video_path": str(tmp_path / "reel.mp4"),
            "duration_s": 12.0,
            "beat_count": 5,
        }
    return _renderer


async def _produce(tmp_path, monkeypatch, uploader):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return await app_module.research_to_reel(
        source_execution_id=_EXEC_ID,
        selected_paragraphs=[{"paragraph_id": "0-0", "text": "Body.", "position": 0}],
        out_dir=str(tmp_path),
        fetch_body=_fetch_ok,
        distiller=_distiller,
        composer=_composer,
        renderer=_make_renderer(tmp_path),
        uploader=uploader,
    )


async def test_result_ref_is_bucket_url_when_delivered(tmp_path, monkeypatch):
    result = await _produce(
        tmp_path, monkeypatch,
        uploader=lambda p, *, run_id: f"https://s3.example/bkt/outputs/{run_id}/reel.mp4?X-Amz-Expires=1",
    )
    # OBSERVE via the production read path (never a raw store read).
    ref = _resolve_result_ref(_EXEC_ID, {"result": result})
    assert ref.startswith("https://")            # downloadable bucket URL


async def test_result_ref_is_placeholder_when_delivery_disabled(tmp_path, monkeypatch):
    # RED-AT-SEAM: disable delivery → no download_url → placeholder, not a bucket URL.
    result = await _produce(tmp_path, monkeypatch, uploader=lambda p, *, run_id: None)
    ref = _resolve_result_ref(_EXEC_ID, {"result": result})
    assert ref == f"cp-execution://{_EXEC_ID}/result/video_path"
    assert not ref.startswith("http")
