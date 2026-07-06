"""B10/B11 — the composite pipeline: ingest → build base reel → finish.

``compile → stitch → finish`` runs by default. This module is the thin
orchestrator the ``reel-af composite`` CLI (B11) and the ``composite_to_reel``
reasoner (B10) call. The richer :func:`finish_reel` runs **by default**;
``raw=True`` (``--fast``) yields the plain stitched reel.

Stages are injectable (:class:`CompositeStages`) so the ordering and the
default/fast branch are unit-testable without ffmpeg or a network. The default
stages wire the real pieces:
  - ingest → ``hooks.download_crisp_source`` (B1 crisp, vertical-correct fetch)
  - finish → :func:`finish_reel` (B9)
  - build_base_reel → the DSL compile + footage-stitch subsystem

The heavy URL→CompositeDoc→FootageReel selection lives in the existing DSL
subsystem; ``build_base_reel`` is the seam onto it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from reel_af.render.finish import FinishContext, ReelFinishConfig, finish_reel


@dataclass
class BaseReel:
    """The plain stitched reel plus the transcript the finish stage needs."""

    path: Path
    transcript: str


@dataclass
class CompositeStages:
    """Injectable pipeline stages — real modules by default, spies in tests."""

    ingest: Callable[[str, Path], Awaitable[Path]]
    build_base_reel: Callable[[Path, Path], Awaitable[BaseReel]]
    finish: Callable[..., Awaitable[Path]]


async def composite_to_reel(
    source_url: str,
    out_dir: Path,
    *,
    provider: Any = None,
    cfg: Optional[ReelFinishConfig] = None,
    raw: bool = False,
    stages: Optional[CompositeStages] = None,
    run_id: str = "composite",
) -> Path:
    """Ingest a crisp source, stitch the base reel, then finish it (default).

    ``raw=True`` (``--fast``) returns the plain stitched reel and skips finish.
    """
    stages = stages or default_stages()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    source = await stages.ingest(source_url, out_dir)
    base = await stages.build_base_reel(source, out_dir)

    if raw:
        return base.path

    ctx = FinishContext(
        transcript=base.transcript,
        provider=provider,
        source_url=source_url,
        run_id=run_id,
    )
    return await stages.finish(base.path, ctx, cfg, out_dir=out_dir)


def default_stages() -> CompositeStages:
    """Wire the real ingest (B1) + finish (B9); build-base bridges the DSL subsystem."""
    return CompositeStages(
        ingest=_crisp_ingest,
        build_base_reel=_compile_and_stitch,
        finish=finish_reel,
    )


async def _crisp_ingest(source_url: str, out_dir: Path) -> Path:
    """Download the source at native vertical resolution (B1, off-thread)."""
    import asyncio

    from reel_af.render import hooks

    dest = Path(out_dir) / "source.mp4"
    return await asyncio.to_thread(hooks.download_crisp_source, source_url, dest)


async def _compile_and_stitch(source: Path, out_dir: Path) -> BaseReel:
    """Seam onto the existing DSL compile + footage-stitch subsystem.

    Producing a ``CompositeDoc`` from a raw source (whisper + window/cut
    selection) is the existing DSL subsystem's job, not this front-end's; a
    concrete build stage is injected by the caller that owns that selection.
    """
    raise NotImplementedError(
        "composite_to_reel.default build_base_reel: provide a build_base_reel "
        "stage that compiles + stitches your CompositeDoc into a base reel "
        "(compile_composite → stitch_footage_reel), or inject CompositeStages."
    )


__all__ = [
    "BaseReel",
    "CompositeStages",
    "composite_to_reel",
    "default_stages",
]
