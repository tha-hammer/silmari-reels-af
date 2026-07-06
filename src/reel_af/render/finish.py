"""B9 — ``finish_reel``: the default richer finish on every real-footage reel.

Takes a plain stitched reel and burns the three proven enhancements on top in a
single ffmpeg pass:

  1. **Banner** — an LLM hook line, boxed lime text on the divider bar.
  2. **Captions** — whisper the *final* reel's audio, group words into short
     phrases, burn them in the safe zone (≈70% height).
  3. **Image cut-ins** — 2-3 context images generated from abstract beats,
     scaled/cropped into the screenshare pane and overlaid for ~2-3s each.

The closure composes the sibling pieces (it does not re-implement them):
  - captions (B2/B3/B4): ``caption_words`` + ``build_finish_ass`` + ``write_ass``
  - hooks (B5/B6): ``generate_hook`` + ``pick_image_moments``
  - image_cutins (B7/B8): ``generate_image_cutins`` + ``build_image_overlay_filtergraph``

CobaltMeadow's overlay filtergraph produces the video label; this module appends
the ASS burn as the final filter stage so captions sit on top of the image
cut-ins, then runs one encode. Every tunable comes from :class:`ReelFinishConfig`
(B0) — no magic literals here.

Collaborators are injected via :class:`FinishDeps` so the orchestration is
unit-testable with fakes; :func:`default_deps` lazily wires the real modules.

``raw=True`` (the ``--fast`` opt-out) skips everything and returns the plain
stitched reel unchanged — richer is the default, plain is opt-in.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from reel_af.render.finish_config import (
    AssStyle,
    ImageRegion,
    ReelFinishConfig,
    banner_pos_tag,
    caption_pos_tag,
)

CaptionWord = tuple[float, float, str]

FFMPEG_FINISH_TIMEOUT_S: float = 300.0


# ── Context + deps ─────────────────────────────────────────────────


@dataclass
class FinishContext:
    """Per-reel inputs the finish stage needs beyond the base mp4."""

    transcript: str
    provider: Any
    source_url: Optional[str] = None
    run_id: str = "finish"


@dataclass
class FinishDeps:
    """Injectable collaborators — real sibling modules by default, fakes in tests."""

    caption_words: Callable[[Path, ReelFinishConfig], list[CaptionWord]]    # B2
    build_finish_ass: Callable[..., str]                                   # B3+B4
    write_ass: Callable[[str, Path], Path]
    generate_hook: Callable[[str, Any], Awaitable[str]]                    # B5
    pick_image_moments: Callable[..., Awaitable[Sequence[Any]]]           # B6
    generate_image_cutins: Callable[[Any, Sequence[Any], Path], Awaitable[list[Any]]]  # B7
    build_overlay_graph: Callable[[Sequence[Any], ReelFinishConfig], Any]  # B8
    image_paths_for_cutins: Callable[[Sequence[Any]], list[Path]]
    run_ffmpeg: Callable[[Sequence[str], float], Awaitable[None]]
    probe_duration: Callable[[Path], float]


def default_deps() -> FinishDeps:
    """Wire the real sibling modules. Imported lazily to keep unit tests light."""
    from reel_af.render import captions, hooks, image_cutins  # noqa: WPS433

    async def pick_moments(transcript: str, provider: Any, cfg: ReelFinishConfig, duration_s: float):
        return await hooks.pick_image_moments(
            transcript, provider, cfg, duration_s=duration_s
        )

    async def gen_cutins(provider: Any, cut_ins: Sequence[Any], out_dir: Path):
        return await image_cutins.generate_image_cutins(
            provider, cut_ins, out_dir=out_dir
        )

    def overlay_graph(cut_ins: Sequence[Any], cfg: ReelFinishConfig):
        return image_cutins.build_image_overlay_filtergraph(cut_ins, config=cfg)

    return FinishDeps(
        caption_words=captions.caption_words,
        build_finish_ass=captions.build_finish_ass,
        write_ass=captions.write_ass,
        generate_hook=hooks.generate_hook,
        pick_image_moments=pick_moments,
        generate_image_cutins=gen_cutins,
        build_overlay_graph=overlay_graph,
        image_paths_for_cutins=image_cutins.image_paths_for_cutins,
        run_ffmpeg=_run_ffmpeg,
        probe_duration=probe_duration,
    )


# ── Pure ffmpeg composition ────────────────────────────────────────


def compose_finish_filtergraph(
    overlay_filter_complex: str,
    overlay_label: str,
    ass_path: Optional[Path],
) -> tuple[str, str]:
    """Append the ASS burn to an image-overlay filtergraph.

    ``overlay_label`` is the video label emitted by the overlay graph (e.g.
    ``[v]``). Returns ``(filter_complex, output_map_label)``. With no ASS the
    overlay graph passes through unchanged (captions are always present in
    practice, but this keeps the builder total).
    """
    if ass_path is None:
        return overlay_filter_complex, overlay_label
    inner = overlay_label.strip("[]")
    fc = f"{overlay_filter_complex};[{inner}]ass={_ass_filter_arg(ass_path)}[vout]"
    return fc, "[vout]"


def build_finish_ffmpeg_cmd(
    base: Path,
    image_paths: Sequence[Path],
    overlay_filter_complex: str,
    overlay_label: str,
    ass_path: Optional[Path],
    out_path: Path,
    duration_s: float,
    cfg: ReelFinishConfig,
) -> list[str]:
    """Full ffmpeg command: base + looped still images, overlay+ass graph, copy audio."""
    filter_complex, vlabel = compose_finish_filtergraph(
        overlay_filter_complex, overlay_label, ass_path
    )
    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(base)]
    for img in image_paths:
        cmd += ["-loop", "1", "-i", str(img)]
    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        vlabel,
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-crf",
        str(cfg.encode_crf),
        "-preset",
        cfg.encode_preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-t",
        f"{duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return cmd


def _ass_filter_arg(ass_path: Path) -> str:
    """Escape an ASS path for use inside an ffmpeg filter argument."""
    s = str(ass_path)
    # ffmpeg filter parsing treats ':' and '\' specially inside filenames.
    s = s.replace("\\", "\\\\").replace(":", "\\:")
    return s


# ── Orchestration ──────────────────────────────────────────────────


async def finish_reel(
    base: Path,
    ctx: FinishContext,
    cfg: Optional[ReelFinishConfig] = None,
    *,
    deps: Optional[FinishDeps] = None,
    raw: bool = False,
    out_dir: Optional[Path] = None,
) -> Path:
    """Burn banner + captions + image cut-ins onto ``base`` and return the mp4.

    ``raw=True`` (``--fast``) returns ``base`` untouched — the plain stitched reel.
    """
    base = Path(base)
    if raw:
        return base

    cfg = cfg or ReelFinishConfig()
    deps = deps or default_deps()
    out_dir = Path(out_dir) if out_dir is not None else base.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    dur = float(deps.probe_duration(base))

    # 1. Hook (LLM) + caption timings (whisper) — independent, run concurrently.
    hook, words = await asyncio.gather(
        deps.generate_hook(ctx.transcript, ctx.provider),
        asyncio.to_thread(deps.caption_words, base, cfg),
    )

    # 2. Banner + captions into one combined ASS file (B3+B4).
    ass_text = deps.build_finish_ass(words, hook, dur, cfg)
    ass_path = deps.write_ass(ass_text, out_dir / f"{ctx.run_id}.ass")

    # 3. Image cut-ins (optional, config-gated) — pick moments, generate images.
    cut_ins: list[Any] = []
    if cfg.image_count > 0:
        moments = await deps.pick_image_moments(
            ctx.transcript, ctx.provider, cfg, dur
        )
        raw_cutins = [
            {"t_start": m[0], "t_end": m[1], "image_prompt": m[2]} for m in moments
        ]
        if raw_cutins:
            cut_ins = list(
                await deps.generate_image_cutins(
                    ctx.provider, raw_cutins, out_dir / f"{ctx.run_id}-images"
                )
            )

    # 4. Compose overlay graph + ass burn into one ffmpeg pass.
    graph = deps.build_overlay_graph(cut_ins, cfg)
    image_paths = deps.image_paths_for_cutins(cut_ins) if cut_ins else []
    out_path = out_dir / "final.mp4"
    cmd = build_finish_ffmpeg_cmd(
        base=base,
        image_paths=image_paths,
        overlay_filter_complex=graph.filter_complex,
        overlay_label=graph.video_label,
        ass_path=Path(ass_path),
        out_path=out_path,
        duration_s=dur,
        cfg=cfg,
    )
    await deps.run_ffmpeg(cmd, FFMPEG_FINISH_TIMEOUT_S)
    return out_path


# ── Real-dep helpers (used by default_deps) ────────────────────────


def probe_duration(path: Path) -> float:
    """ffprobe the container duration in seconds."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


async def _run_ffmpeg(cmd: Sequence[str], timeout_s: float) -> None:
    """Run an ffmpeg command, raising with stderr on nonzero exit."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("finish_reel requires ffmpeg on PATH")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError):
        proc.kill()
        await proc.communicate()
        raise TimeoutError(f"finish_reel ffmpeg timed out after {timeout_s:.1f}s") from None
    if proc.returncode != 0:
        raise RuntimeError(
            f"finish_reel ffmpeg failed (exit {proc.returncode}): "
            f"{stderr_bytes.decode(errors='replace')[-800:]}"
        )


__all__ = [
    "AssStyle",
    "ImageRegion",
    "ReelFinishConfig",
    "FinishContext",
    "FinishDeps",
    "banner_pos_tag",
    "caption_pos_tag",
    "compose_finish_filtergraph",
    "build_finish_ffmpeg_cmd",
    "default_deps",
    "finish_reel",
    "probe_duration",
]
