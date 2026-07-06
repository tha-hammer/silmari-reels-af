"""B10 — default wiring + ``--fast`` opt-out for the composite pipeline.

``composite_to_reel`` runs ingest → build-base-reel → finish. The richer
``finish_reel`` runs BY DEFAULT; ``raw=True`` (the ``--fast`` opt-out) returns
the plain stitched reel and never touches finish. Stages are injectable so the
ordering and the default/fast branch are provable without ffmpeg or a network.
"""

from __future__ import annotations

from pathlib import Path

from reel_af.render.composite_pipeline import (
    BaseReel,
    CompositeStages,
    composite_to_reel,
)


def _spy_stages(calls: list[str]) -> CompositeStages:
    async def ingest(url, out_dir):
        calls.append(f"ingest:{url}")
        return Path(out_dir) / "source.mp4"

    async def build_base_reel(source, out_dir):
        calls.append(f"build:{source.name}")
        return BaseReel(path=Path(out_dir) / "base.mp4", transcript="the transcript")

    async def finish(base, ctx, cfg, *, out_dir):
        calls.append(f"finish:{base.name}:{ctx.transcript}")
        return Path(out_dir) / "final.mp4"

    return CompositeStages(ingest=ingest, build_base_reel=build_base_reel, finish=finish)


async def test_default_path_runs_ingest_build_finish_in_order(tmp_path) -> None:
    calls: list[str] = []
    out = await composite_to_reel(
        "http://youtu.be/x", tmp_path, stages=_spy_stages(calls)
    )
    assert calls == [
        "ingest:http://youtu.be/x",
        "build:source.mp4",
        "finish:base.mp4:the transcript",
    ]
    assert out == tmp_path / "final.mp4"


async def test_fast_opt_out_skips_finish_and_returns_base(tmp_path) -> None:
    calls: list[str] = []
    out = await composite_to_reel(
        "http://youtu.be/x", tmp_path, stages=_spy_stages(calls), raw=True
    )
    # finish is never invoked on the fast path.
    assert not any(c.startswith("finish") for c in calls)
    assert calls == ["ingest:http://youtu.be/x", "build:source.mp4"]
    assert out == tmp_path / "base.mp4"


async def test_transcript_and_source_url_flow_into_finish_context(tmp_path) -> None:
    seen = {}

    async def ingest(url, out_dir):
        return Path(out_dir) / "source.mp4"

    async def build_base_reel(source, out_dir):
        return BaseReel(path=Path(out_dir) / "base.mp4", transcript="hello world")

    async def finish(base, ctx, cfg, *, out_dir):
        seen["url"] = ctx.source_url
        seen["transcript"] = ctx.transcript
        return Path(out_dir) / "final.mp4"

    await composite_to_reel(
        "http://youtu.be/abc",
        tmp_path,
        stages=CompositeStages(ingest=ingest, build_base_reel=build_base_reel, finish=finish),
    )
    assert seen == {"url": "http://youtu.be/abc", "transcript": "hello world"}


async def test_default_stages_wire_real_ingest_and_finish() -> None:
    from reel_af.render.composite_pipeline import default_stages
    from reel_af.render.finish import finish_reel

    stages = default_stages()
    # Defaults point at the real B1 crisp ingest and the real B9 finish.
    assert stages.finish is finish_reel
    assert callable(stages.ingest)
    assert callable(stages.build_base_reel)
