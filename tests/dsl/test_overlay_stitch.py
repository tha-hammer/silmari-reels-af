from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reel_af.dsl.models import (
    DownloadedSegment,
    FootageReel,
    SourceSegment,
    Transition,
)

# ── builders ──────────────────────────────────────────────────────


def _source_reel(specs: list[tuple[str, float, float]]) -> FootageReel:
    segments = [
        SourceSegment(segment_id=sid, source_url="fixture", start_s=a, end_s=b, text=sid)
        for sid, a, b in specs
    ]
    transitions = [
        Transition(before_index=i, after_index=i + 1, effect="none", duration_s=0.0)
        for i in range(len(specs) - 1)
    ]
    duration = sum(b - a for _, a, b in specs)
    return FootageReel(
        source_url="fixture",
        segments=segments,
        transitions=transitions,
        duration_s=duration,
    )


def _asset(seg_id: str, start_s: float, end_s: float, path: Path) -> DownloadedSegment:
    return DownloadedSegment(
        segment_id=seg_id, path=path, source_start_s=start_s, source_end_s=end_s
    )


def _src_file(tmp_path: Path, name: str) -> Path:
    p = tmp_path / "src" / f"{name}.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"source-media")
    return p


class _RenderSpy:
    """Stand-in for render_overlay_clip that records calls and writes a stub."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.max_concurrent = 0
        self._active = 0

    async def __call__(
        self,
        segment_path,
        cut_ins,
        visual_images,
        out_path,
        *,
        segment_start_s,
        segment_duration_s=None,
        timeout_s=120.0,
    ):
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        await asyncio.sleep(0.01)
        self.calls.append(
            {
                "segment_path": Path(segment_path),
                "cut_ins": list(cut_ins),
                "visual_images": list(visual_images),
                "out_path": Path(out_path),
                "segment_start_s": segment_start_s,
                "segment_duration_s": segment_duration_s,
            }
        )
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"overlaid")
        self._active -= 1
        return Path(out_path)


async def _provider_never(prompt, idx, images_dir):  # pragma: no cover - asserts unused
    raise AssertionError("image provider should not be called")


# ── B1 · empty plan / empty lists are identity ───────────────────


async def test_apply_overlays_empty_plan_is_identity(tmp_path, monkeypatch):
    from reel_af.render import overlay_stitch

    reel = _source_reel([("s1", 0.0, 1.0)])
    assets = {"s1": _asset("s1", 0.0, 1.0, _src_file(tmp_path, "s1"))}
    spy = _RenderSpy()
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", spy)

    result = await overlay_stitch.apply_overlays(
        reel, assets, {}, tmp_path / "out", "run-1", image_provider=_provider_never
    )

    assert result == assets
    assert result is not assets
    assert spy.calls == []
    assert result["s1"].pre_normalized is False


async def test_apply_overlays_empty_cutin_list_is_identity(tmp_path, monkeypatch):
    from reel_af.render import overlay_stitch

    reel = _source_reel([("s1", 0.0, 1.0)])
    assets = {"s1": _asset("s1", 0.0, 1.0, _src_file(tmp_path, "s1"))}
    spy = _RenderSpy()
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", spy)

    result = await overlay_stitch.apply_overlays(
        reel, assets, {"s1": []}, tmp_path / "out", "run-1", image_provider=_provider_never
    )

    assert spy.calls == []
    assert result["s1"] is assets["s1"]
    assert assets["s1"].pre_normalized is False


# ── B2 · zoom cut-in swaps one asset, marks pre_normalized ────────


async def test_apply_overlays_zoom_swaps_and_marks(tmp_path, monkeypatch):
    from reel_af.render import overlay_stitch

    SOURCE_START_S = 10.0
    AT_S = SOURCE_START_S + 1.0
    UNTIL_S = AT_S + 2.0
    SOURCE_END_S = 13.0

    reel = _source_reel([("s1", 10.0, 13.0), ("s2", 13.0, 14.0)])
    src1 = _src_file(tmp_path, "s1")
    assets = {
        "s1": _asset("s1", SOURCE_START_S, SOURCE_END_S, src1),
        "s2": _asset("s2", 13.0, 14.0, _src_file(tmp_path, "s2")),
    }
    spy = _RenderSpy()
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", spy)

    plan = {"s1": [{"type": "zoom", "at_s": AT_S, "until_s": UNTIL_S}]}
    result = await overlay_stitch.apply_overlays(
        reel, assets, plan, tmp_path / "out", "run-1", image_provider=_provider_never
    )

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["segment_path"] == src1
    assert call["segment_start_s"] == SOURCE_START_S
    assert call["visual_images"] == []
    # result is a fresh mapping; s1 swapped + pre_normalized; s2 untouched
    assert result is not assets
    assert result["s1"].path == call["out_path"]
    assert result["s1"].pre_normalized is True
    assert result["s2"] == assets["s2"]
    # input mapping untouched
    assert assets["s1"].path == src1
    assert assets["s1"].pre_normalized is False


# ── B3 · images only for active visual windows, correct order ─────


async def test_apply_overlays_generates_images_only_for_active_visuals(
    tmp_path, monkeypatch
):
    from reel_af.render import overlay_stitch
    from reel_af.render.overlays import build_overlay_filtergraph

    reel = _source_reel([("s1", 1.0, 3.0)])
    assets = {"s1": _asset("s1", 1.0, 3.0, _src_file(tmp_path, "s1"))}
    spy = _RenderSpy()
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", spy)

    provider_calls: list[tuple[str, int]] = []

    async def provider(prompt, idx, images_dir):
        provider_calls.append((prompt, idx))
        Path(images_dir).mkdir(parents=True, exist_ok=True)
        img = Path(images_dir) / f"img-{idx}.png"
        img.write_bytes(b"png")
        return img

    full_cut_ins = [
        {"type": "visual", "at_s": 0.0, "until_s": 0.4, "image_prompt": "before"},
        {"type": "visual", "at_s": 2.6, "until_s": 2.9, "image_prompt": "later"},
        {"type": "visual", "at_s": 1.6, "until_s": 2.0, "image_prompt": "earlier"},
    ]
    plan = {"s1": full_cut_ins}
    await overlay_stitch.apply_overlays(
        reel, assets, plan, tmp_path / "out", "run-1", image_provider=provider
    )

    # "before" clamps out; active visuals ordered by at_s → earlier(1.6), later(2.6)
    assert [p for p, _ in provider_calls] == ["earlier", "later"]
    assert [i for _, i in provider_calls] == [0, 1]

    graph = build_overlay_filtergraph(
        full_cut_ins, segment_start_s=1.0, segment_duration_s=2.0
    )
    assert len(spy.calls[0]["visual_images"]) == graph.visual_input_count == 2


# ── B5 · fully-inactive non-empty list still renders once ─────────


async def test_apply_overlays_inactive_list_still_renders_for_normalization(
    tmp_path, monkeypatch
):
    from reel_af.render import overlay_stitch

    reel = _source_reel([("s1", 1.0, 3.0)])
    assets = {"s1": _asset("s1", 1.0, 3.0, _src_file(tmp_path, "s1"))}
    spy = _RenderSpy()
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", spy)

    provider_calls: list[str] = []

    async def provider(prompt, idx, images_dir):
        provider_calls.append(prompt)
        return Path("unused")

    plan = {
        "s1": [
            {"type": "zoom", "at_s": 100.0, "until_s": 101.0},
            {"type": "visual", "at_s": 100.0, "until_s": 101.0, "image_prompt": "x"},
        ]
    }
    result = await overlay_stitch.apply_overlays(
        reel, assets, plan, tmp_path / "out", "run-1", image_provider=provider
    )

    assert provider_calls == []
    assert len(spy.calls) == 1
    assert spy.calls[0]["visual_images"] == []
    assert result["s1"].pre_normalized is True


# ── B6 · guards + failure propagation + immutability ──────────────


async def test_apply_overlays_rejects_unknown_segment_id(tmp_path, monkeypatch):
    from reel_af.render import overlay_stitch

    reel = _source_reel([("s1", 0.0, 1.0)])
    assets = {"s1": _asset("s1", 0.0, 1.0, _src_file(tmp_path, "s1"))}
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", _RenderSpy())

    with pytest.raises(overlay_stitch.OverlayPlanError):
        await overlay_stitch.apply_overlays(
            reel,
            assets,
            {"ghost": [{"type": "zoom", "at_s": 0.0, "until_s": 1.0}]},
            tmp_path / "out",
            "run-1",
            image_provider=_provider_never,
        )


async def test_apply_overlays_provider_failure_propagates_and_preserves_input(
    tmp_path, monkeypatch
):
    from reel_af.render import overlay_stitch

    reel = _source_reel([("s1", 1.0, 3.0)])
    src1 = _src_file(tmp_path, "s1")
    assets = {"s1": _asset("s1", 1.0, 3.0, src1)}
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", _RenderSpy())

    async def boom(prompt, idx, images_dir):
        raise RuntimeError("provider down")

    plan = {"s1": [{"type": "visual", "at_s": 1.5, "until_s": 2.5, "image_prompt": "p"}]}
    with pytest.raises(RuntimeError, match="provider down"):
        await overlay_stitch.apply_overlays(
            reel, assets, plan, tmp_path / "out", "run-1", image_provider=boom
        )
    # caller's mapping untouched
    assert assets["s1"].path == src1
    assert assets["s1"].pre_normalized is False


# ── B7 · bounded concurrency, env knob, unique paths ──────────────


def test_overlay_stitch_concurrency_env(monkeypatch):
    from reel_af.render import overlay_stitch

    monkeypatch.delenv("REEL_AF_OVERLAY_STITCH_CONCURRENCY", raising=False)
    assert overlay_stitch.overlay_stitch_concurrency() == 4
    monkeypatch.setenv("REEL_AF_OVERLAY_STITCH_CONCURRENCY", "3")
    assert overlay_stitch.overlay_stitch_concurrency() == 3
    monkeypatch.setenv("REEL_AF_OVERLAY_STITCH_CONCURRENCY", "0")
    assert overlay_stitch.overlay_stitch_concurrency() == 4
    monkeypatch.setenv("REEL_AF_OVERLAY_STITCH_CONCURRENCY", "not-an-int")
    assert overlay_stitch.overlay_stitch_concurrency() == 4


async def test_apply_overlays_bounded_concurrency_and_unique_paths(tmp_path, monkeypatch):
    from reel_af.render import overlay_stitch

    specs = [(f"s{i}", float(i), float(i) + 1.0) for i in range(4)]
    reel = _source_reel(specs)
    assets = {
        sid: _asset(sid, a, b, _src_file(tmp_path, sid)) for sid, a, b in specs
    }
    spy = _RenderSpy()
    monkeypatch.setattr(overlay_stitch, "render_overlay_clip", spy)

    plan = {
        sid: [{"type": "zoom", "at_s": a + 0.1, "until_s": a + 0.9}]
        for sid, a, b in specs
    }
    result = await overlay_stitch.apply_overlays(
        reel, assets, plan, tmp_path / "out", "run-1", image_provider=_provider_never,
        concurrency=2,
    )

    assert spy.max_concurrent <= 2
    assert set(result.keys()) == set(assets.keys())
    out_paths = {result[sid].path for sid, _, _ in specs}
    assert len(out_paths) == 4
    src_paths = {assets[sid].path for sid, _, _ in specs}
    assert out_paths.isdisjoint(src_paths)
    for sid, _, _ in specs:
        assert result[sid].pre_normalized is True
