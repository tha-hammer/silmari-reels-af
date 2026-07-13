"""MW Phase 3 B1 — reel-af ``research_to_reel`` reasoner (registered id ``reel_research_to_reel``).

Closure test (ENHANCED plan §B1):
  TRIGGER    dispatch ``research_to_reel({source_execution_id, selection})``
  SOURCE     fetch_body → essence_from_text (grounded on the SELECTED text, skip URL fetch)
             → compose_script (REQUIRED) → _render_downstream
  OBSERVABLE a reel is produced carrying ``source_run_id`` + ``citations`` in metadata  [C6]

Uses reel-af's keyword-seam injection so the whole behavior runs with NO real infra
(no OpenRouter key, no control plane, no TTS/ffmpeg): ``fetch_body``, ``distiller``,
``composer``, ``renderer`` are all injected. The red-at-seam test disables the
``fetch_body`` seam (S3) and asserts the reasoner fails with ``source_unavailable``
and never renders a partial reel.
"""

from __future__ import annotations

import types
from pathlib import Path

from reel_af.models import Essence

_CITATIONS = [
    {"citationId": 1, "url": "https://nature.com/x", "domain": "nature.com", "title": "T"},
]

# sections[sectionIndex].content, split on double-newline → paragraphs[paragraphIndex]
_RESEARCH_PACKAGE = {
    "sections": [
        {"content": "First paragraph body.\n\nSecond paragraph body."},
        {"content": "Later section paragraph."},
    ]
}


def _fake_record(execution_id: str = "exec_abc123", run_id: str = "run_abc"):
    """Duck-typed stand-in for agentfield.handoff ExecutionRecord (which is NOT
    importable in the test venv). The reasoner reads ``record.result``."""
    return types.SimpleNamespace(
        execution_id=execution_id,
        status="succeeded",
        run_id=run_id,
        result={"research_package": _RESEARCH_PACKAGE},
    )


def _fetch_ok(_execution_id):
    return _fake_record()


def _fetch_boom(_execution_id):
    raise RuntimeError("404 execution not found")


async def _fake_distiller(text):
    _fake_distiller.seen = text
    return Essence(
        core_claim="c",
        mechanism="m",
        evidence=["e"],
        content_mode="general",
        domain="tech",
    )


def _fake_composer(_node, essence):
    _fake_composer.seen_essence = essence
    return {"hook": "h", "hook_variant": "a", "beats": []}


def _make_renderer():
    calls = {"n": 0}

    async def _renderer(**kwargs):
        calls["n"] += 1
        return {
            "video_path": str(Path(kwargs["out_path"]) / "reel.mp4"),
            "duration_s": 12.0,
            "narration": "n",
            "voice_id": "v",
            "beat_count": 5,
            "card_count": 3,
            "accent_count": 1,
        }

    return _renderer, calls


def _para(pid, text, position):
    return {"paragraph_id": pid, "text": text, "position": position}


# ─────────────────────────── registration ───────────────────────────


def test_research_to_reel_is_registered():
    import reel_af.app as app_module

    names = [r["wrapper"].__name__ for r in app_module.reel.reasoners]
    assert "research_to_reel" in names


# ─────────────────────────── fails-closed guards ───────────────────────────


async def test_missing_api_key_returns_house_error(monkeypatch):
    import reel_af.app as app_module

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    out = await app_module.research_to_reel(
        source_execution_id="exec_abc123",
        selected_paragraphs=[_para("0-0", "x", 0)],
        fetch_body=_fetch_ok,
    )
    assert out == {"error": "OPENROUTER_API_KEY not set in env."}


# ─────────────────────────── OBSERVABLE: reel + provenance ───────────────────────────


async def test_produces_reel_with_source_run_id_and_citations(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    renderer, calls = _make_renderer()

    out = await app_module.research_to_reel(
        source_execution_id="exec_abc123",
        selected_paragraphs=[_para("0-0", "First paragraph body.", 0)],
        source_run_id="run_abc",
        citations=_CITATIONS,
        out_dir=str(tmp_path),
        fetch_body=_fetch_ok,
        distiller=_fake_distiller,
        composer=_fake_composer,
        renderer=renderer,
    )

    assert "error" not in out
    assert out["video_path"].endswith("reel.mp4")     # reel produced
    assert out["source"] == "research"
    assert out["source_run_id"] == "run_abc"           # provenance in metadata (C6)
    assert out["source_execution_id"] == "exec_abc123"
    assert out["citations"] == _CITATIONS              # citations preserved (C6)
    assert out["beat_count"] == 5
    assert calls["n"] == 1                             # rendered exactly once


async def test_grounds_essence_on_selected_text_in_document_order(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    renderer, _ = _make_renderer()

    # deliberately out of position order → reasoner must sort by position
    await app_module.research_to_reel(
        source_execution_id="exec_abc123",
        selected_paragraphs=[
            _para("0-1", "Second paragraph body.", 1),
            _para("0-0", "First paragraph body.", 0),
        ],
        fetch_body=_fetch_ok,
        distiller=_fake_distiller,
        composer=_fake_composer,
        renderer=renderer,
        out_dir=str(tmp_path),
    )
    assert _fake_distiller.seen == "First paragraph body.\n\nSecond paragraph body."


async def test_compose_script_is_not_skipped(tmp_path: Path, monkeypatch):
    """C-2 guard: the pipeline must run compose_script between essence and render."""
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    renderer, _ = _make_renderer()
    _fake_composer.seen_essence = None

    await app_module.research_to_reel(
        source_execution_id="exec_abc123",
        selected_paragraphs=[_para("0-0", "First paragraph body.", 0)],
        fetch_body=_fetch_ok,
        distiller=_fake_distiller,
        composer=_fake_composer,
        renderer=renderer,
        out_dir=str(tmp_path),
    )
    # composer ran and received the extracted essence dict (not the raw model)
    assert isinstance(_fake_composer.seen_essence, dict)
    assert _fake_composer.seen_essence["core_claim"] == "c"


async def test_paragraph_text_resolved_from_research_package_when_not_inline(
    tmp_path: Path, monkeypatch
):
    """Extraction spec: a paragraph carrying no inline text is resolved from
    ``result.research_package.sections[sectionIndex]`` split on double-newline."""
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    renderer, _ = _make_renderer()

    await app_module.research_to_reel(
        source_execution_id="exec_abc123",
        selected_paragraphs=[{"paragraph_id": "0-1", "position": 0}],  # no inline text
        fetch_body=_fetch_ok,
        distiller=_fake_distiller,
        composer=_fake_composer,
        renderer=renderer,
        out_dir=str(tmp_path),
    )
    assert _fake_distiller.seen == "Second paragraph body."


# ─────────────────────────── red-at-seam (S3: fetch_body) ───────────────────────────


async def test_red_at_seam_fetch_body_unavailable(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    renderer, calls = _make_renderer()

    out = await app_module.research_to_reel(
        source_execution_id="exec_missing",
        selected_paragraphs=[_para("0-0", "First paragraph body.", 0)],
        fetch_body=_fetch_boom,          # seam disabled → CP 404/unreachable
        distiller=_fake_distiller,
        composer=_fake_composer,
        renderer=renderer,
        out_dir=str(tmp_path),
    )
    assert out["error"] == "source_unavailable"        # clear failure
    assert out["source_execution_id"] == "exec_missing"
    assert calls["n"] == 0                             # no partial reel rendered


async def test_unknown_paragraph_id_fails_closed(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    renderer, calls = _make_renderer()

    out = await app_module.research_to_reel(
        source_execution_id="exec_abc123",
        selected_paragraphs=[{"paragraph_id": "9-9", "position": 0}],  # not in package, no text
        fetch_body=_fetch_ok,
        distiller=_fake_distiller,
        composer=_fake_composer,
        renderer=renderer,
        out_dir=str(tmp_path),
    )
    assert out["error"] == "unknown_paragraph_id"
    assert calls["n"] == 0
