"""AF-4tu: bounded-concurrency beat rendering (OOM fix).

Each beat is a full 1080x1920 encode; rendering all at once OOM-kills ffmpeg
(SIGKILL/-9) on a small node. _render_beats caps simultaneous renders with a
semaphore. Here we prove the cap holds and the config helper resolves correctly,
with _render_beat monkeypatched so no real ffmpeg runs.
"""

from __future__ import annotations

import asyncio
import types

from reel_af.render import stitch


def test_max_beat_concurrency_default(monkeypatch):
    monkeypatch.delenv("REEL_STITCH_CONCURRENCY", raising=False)
    assert stitch._max_beat_concurrency() == 2


def test_max_beat_concurrency_env_override(monkeypatch):
    monkeypatch.setenv("REEL_STITCH_CONCURRENCY", "3")
    assert stitch._max_beat_concurrency() == 3


def test_max_beat_concurrency_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("REEL_STITCH_CONCURRENCY", "nope")
    assert stitch._max_beat_concurrency() == 2


async def test_render_beats_caps_concurrency(tmp_path, monkeypatch):
    beats = [types.SimpleNamespace(idx=i) for i in range(6)]
    artifacts = {i: object() for i in range(6)}
    active = 0
    peak = 0

    async def fake_render(*, beat, artifact, out_path):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)   # hold the slot so overlap would show
        active -= 1

    monkeypatch.setattr(stitch, "_render_beat", fake_render)

    paths = await stitch._render_beats(beats, artifacts, tmp_path, concurrency=2)

    assert peak <= 2                                   # never more than the cap at once
    assert len(paths) == 6                             # all beats rendered
    assert paths[3].name == "beat-03-silent.mp4"       # order + naming preserved


async def test_render_beats_missing_artifact_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(stitch, "_render_beat", lambda **k: asyncio.sleep(0))
    beats = [types.SimpleNamespace(idx=0)]
    try:
        await stitch._render_beats(beats, {}, tmp_path, concurrency=2)
        raise AssertionError("expected RuntimeError for missing artifact")
    except RuntimeError as exc:
        assert "no artifact" in str(exc)
