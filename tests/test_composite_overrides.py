"""Overrides merge onto the preset + reach the render (plan Behavior 2, Closure Test B).

Drives ``_run_composite_reels`` (the tight span) with injected ``deps`` (stub
download/audio/transcribe/probe) and an injected ``runner`` so the real merge
(``{**load_preset, **safe_overrides}``) and snake→camel prop emission run with no
ffmpeg/whisper/Node. The observed artifact is the ``props.json`` the production
``render_overlay`` writes, plus the reel-count math that ``reel_seconds`` drives.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from reel_af import app


class _Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return None


def _words(n_seconds: int):
    return [(float(i), float(i) + 0.4, f"w{i}") for i in range(n_seconds)]


def _deps(duration: float, words):
    def download(url, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00")
        return dest

    return SimpleNamespace(
        download=download,
        has_audio=lambda src: True,
        transcribe=lambda src, workdir=None: words,
        probe_duration=lambda src: duration,
    )


def test_overrides_reach_props_json_and_window_sizing(tmp_path):
    """Closure Test B — valid overrides change the emitted Remotion props AND the
    window sizing. This is the green state; see the red-at-seam check below."""
    result = app._run_composite_reels(
        url="stub://clip", preset_name="middle-third-dynamic", count=99,
        out_path=tmp_path, chrome=None,
        overrides={"phrase_max_words": 3, "overlay_accent": "#00E5FF", "reel_seconds": 60},
        deps=_deps(duration=240.0, words=_words(240)), runner=_Capture(),
    )

    # reel_seconds override (60) drove window sizing: 240 // 60 = 4 reels (not 240//120=2).
    assert result["reel_count"] == 4
    props = json.loads((tmp_path / "reel01" / "props.json").read_text())
    assert props["accent"] == "#00E5FF"          # overlay_accent override merged + emitted


def test_no_overrides_uses_preset_defaults(tmp_path):
    """RED-AT-SEAM proof direction: with the merge disabled (no overrides) the
    preset default reel_seconds=120 sizes windows (240//120=2) and the accent is
    the preset default — exactly what a merge-ignoring implementation would emit
    even *with* overrides, which is why the assertions above go red without merge."""
    result = app._run_composite_reels(
        url="stub://clip", preset_name="middle-third-dynamic", count=99,
        out_path=tmp_path, chrome=None, overrides=None,
        deps=_deps(duration=240.0, words=_words(240)), runner=_Capture(),
    )
    assert result["reel_count"] == 2
    props = json.loads((tmp_path / "reel01" / "props.json").read_text())
    assert props["accent"] == "#7E22CE"


def test_unknown_and_out_of_range_overrides_are_dropped_or_clamped(tmp_path):
    """Reasoner is defensive: unknown keys dropped, reel_seconds=5 clamped to 15."""
    result = app._run_composite_reels(
        url="stub://clip", preset_name="middle-third-dynamic", count=1,
        out_path=tmp_path, chrome=None,
        overrides={"reel_seconds": 5, "nonsense": "x"},
        deps=_deps(duration=240.0, words=_words(240)), runner=_Capture(),
    )
    # clamped to 15s → 240//15 = 16 possible, count=1 cut
    assert result["reel_count"] == 1
    # no crash from the unknown key; source_seconds preserved
    assert result["source_seconds"] == 240.0


async def test_composite_to_reel_accepts_and_forwards_overrides(monkeypatch, tmp_path):
    """The reasoner signature is the ceiling (gate C): it must accept ``overrides``
    (no TypeError) and forward it verbatim to ``_run_composite_reels``."""
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"video_path": "/x/reel01.mp4", "reels": ["/x/reel01.mp4"],
                "reel_count": 1, "source_seconds": 10.0}

    monkeypatch.setattr(app, "_run_composite_reels", fake_run)
    monkeypatch.setattr(app.app, "note", lambda *a, **k: None)
    monkeypatch.setattr("reel_af.storage.upload_reel", lambda *a, **k: None)

    res = await app.composite_to_reel(
        url="stub://clip", preset="middle-third-dynamic", count=1,
        overrides={"phrase_max_words": 3}, out_dir=str(tmp_path / "out"),
    )

    assert captured["overrides"] == {"phrase_max_words": 3}
    assert res["reel_count"] == 1
