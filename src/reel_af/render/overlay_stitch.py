"""Overlay-stitch seam: render source-footage cut-ins per segment.

This module is the missing consumer between :func:`download_segments` and
:func:`stitch_footage_reel`. Given a ``FootageReel``, its ``SegmentAssetMap``,
and an already-planned ``OverlayPlan`` (segment id -> cut-ins), it renders the
cut-ins into each affected source segment and returns a fresh asset map whose
overlaid segments are marked ``pre_normalized=True`` so the stitcher spatially
normalizes the 1080x1920 canvas exactly once.

The edit-decision data (the ``OverlayPlan``) is separate from ffmpeg execution;
producing the plan from transcript hooks is a separate upstream slice.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from reel_af.dsl.models import DownloadedSegment, FootageReel, SegmentAssetMap
from reel_af.render.overlays import (
    CutInOverlay,
    active_cut_ins_for_segment,
    normalize_cut_ins,
    render_overlay_clip,
)

OverlayPlan = Mapping[str, "list[CutInOverlay | Mapping[str, Any]]"]
OverlayImageProviderFn = Callable[[str, int, Path], Awaitable[Path]]

OVERLAY_STITCH_CONCURRENCY_DEFAULT = 4
_CONCURRENCY_ENV = "REEL_AF_OVERLAY_STITCH_CONCURRENCY"


class OverlayPlanError(ValueError):
    """Raised when an OverlayPlan references invalid or unknown segments."""


def overlay_stitch_concurrency() -> int:
    """Bound for concurrent per-segment overlay renders.

    Uses ``REEL_AF_OVERLAY_STITCH_CONCURRENCY`` when it is a positive integer,
    otherwise :data:`OVERLAY_STITCH_CONCURRENCY_DEFAULT`.
    """

    raw = os.environ.get(_CONCURRENCY_ENV)
    if raw is None:
        return OVERLAY_STITCH_CONCURRENCY_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return OVERLAY_STITCH_CONCURRENCY_DEFAULT
    return value if value > 0 else OVERLAY_STITCH_CONCURRENCY_DEFAULT


async def apply_overlays(
    reel: FootageReel,
    segment_assets: SegmentAssetMap,
    overlay_plan: OverlayPlan,
    out_dir: Path,
    run_id: str,
    *,
    image_provider: OverlayImageProviderFn,
    concurrency: int | None = None,
) -> dict[str, DownloadedSegment]:
    """Render planned cut-ins into affected source segments.

    Returns a fresh ``SegmentAssetMap``; the caller's mapping is never mutated.
    Segments with a non-empty cut-in list are re-rendered (and marked
    ``pre_normalized=True``); ``{}`` and empty lists are identity.
    """

    source_ids = {
        seg.segment_id
        for seg in reel.segments
        if getattr(seg, "kind", None) == "source"
    }

    # C1 · input guards (flat guard clauses).
    work_ids: list[str] = []
    for segment_id, cut_ins in overlay_plan.items():
        if segment_id not in source_ids:
            raise OverlayPlanError(
                f"overlay plan references non-source/unknown segment id: {segment_id}"
            )
        if segment_id not in segment_assets:
            raise OverlayPlanError(
                f"overlay plan segment id has no downloaded asset: {segment_id}"
            )
        if cut_ins:
            work_ids.append(segment_id)

    result: dict[str, DownloadedSegment] = dict(segment_assets)
    if not work_ids:
        return result

    overlay_run_dir = Path(out_dir) / "overlays" / _safe_component(run_id)
    bound = concurrency if concurrency is not None else overlay_stitch_concurrency()
    semaphore = asyncio.Semaphore(max(1, bound))

    async def _render_one(segment_id: str) -> tuple[str, DownloadedSegment]:
        async with semaphore:
            asset = segment_assets[segment_id]
            overlaid = await _render_segment(
                asset=asset,
                cut_ins=overlay_plan[segment_id],
                overlay_run_dir=overlay_run_dir,
                image_provider=image_provider,
            )
            return segment_id, asset.model_copy(
                update={"path": overlaid, "pre_normalized": True}
            )

    tasks = [asyncio.create_task(_render_one(sid)) for sid in work_ids]
    for segment_id, new_asset in await _gather_or_cancel(tasks):
        result[segment_id] = new_asset
    return result


async def _render_segment(
    *,
    asset: DownloadedSegment,
    cut_ins: "list[CutInOverlay | Mapping[str, Any]]",
    overlay_run_dir: Path,
    image_provider: OverlayImageProviderFn,
) -> Path:
    segment_start_s = asset.source_start_s
    segment_duration_s = asset.source_end_s - asset.source_start_s
    normalized = normalize_cut_ins(cut_ins)

    active = active_cut_ins_for_segment(cut_ins, segment_start_s, segment_duration_s)
    active_visuals = [cut_in for cut_in in active if cut_in.type == "visual"]

    visual_images: list[Path] = []
    if active_visuals:
        images_dir = overlay_run_dir / "images" / _safe_component(asset.segment_id)
        images_dir.mkdir(parents=True, exist_ok=True)
        for idx, cut_in in enumerate(active_visuals):
            image = await image_provider(cut_in.image_prompt or "", idx, images_dir)
            visual_images.append(Path(image))

    out_path = overlay_run_dir / f"{_safe_component(asset.segment_id)}.mp4"
    if out_path == Path(asset.path):
        raise OverlayPlanError(
            f"overlay output path collides with source asset path: {out_path}"
        )
    return await render_overlay_clip(
        asset.path,
        normalized,
        visual_images,
        out_path,
        segment_start_s=segment_start_s,
        segment_duration_s=segment_duration_s,
    )


async def _gather_or_cancel(tasks: list[asyncio.Task]) -> list[Any]:
    """Await all tasks; on the first failure, cancel and drain the rest."""

    if not tasks:
        return []
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    error: BaseException | None = None
    for task in done:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            error = exc
            break
    if error is not None:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending)
        raise error
    return [task.result() for task in tasks]


def openrouter_overlay_image_provider(
    provider: Any,
    *,
    content_mode: str = "general",
    model: str | None = None,
) -> OverlayImageProviderFn:
    """Adapt a production ``OpenRouterProvider`` to an ``OverlayImageProviderFn``."""

    from reel_af.render.images import generate_first_frame

    async def _provide(prompt: str, idx: int, images_dir: Path) -> Path:
        return await generate_first_frame(
            provider=provider,
            image_prompt=prompt,
            idx=idx,
            out_dir=images_dir,
            content_mode=content_mode,
            model=model,
        )

    return _provide


def _safe_component(value: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value).strip()
    )
    return safe or "segment"


__all__ = [
    "OVERLAY_STITCH_CONCURRENCY_DEFAULT",
    "OverlayImageProviderFn",
    "OverlayPlan",
    "OverlayPlanError",
    "apply_overlays",
    "openrouter_overlay_image_provider",
    "overlay_stitch_concurrency",
]
