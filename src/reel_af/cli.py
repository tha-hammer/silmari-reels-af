"""reel-af CLI v3 — URL → vertical reel, maximally parallel.

Pipeline (DeepSeek V4 Pro orchestration, strategic context per stage):

   1. Navigator           ─→ clean source + key claims
                              (only step that sees raw article body)
   2. Story Writer        ─→ ONE 50-word viral script + voice tone
                              (sees claims only, NOT body)
   3. Scene Breaker       ─→ scenes [{ sentence, caption }]
                              (sees script only)
   4. Shot Director (║)   ─┐
      TTS (║)              ├─ parallel: both depend only on scenes
                           │           but neither blocks the other
   5. Video gen (║)       ←┘ runs after director — per-scene parallel
                              ⤷ each scene: grok-imagine → Veo i2v
   6. Assembly (║)        ─→ per-segment ffmpeg renders run in PARALLEL
                              then a single fast concat

The two big wins over v0.2:
  • TTS starts as soon as scenes are available, in parallel with director
  • Per-segment ffmpeg renders run concurrently (was sequential)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Annotated, Optional

import typer
from agentfield import Agent, AIConfig
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from reel_af.agents.distiller import distill
from reel_af.agents.navigator import navigate
from reel_af.agents.scene_breaker import break_scenes
from reel_af.agents.shot_director_v2 import direct_shots_v2
from reel_af.agents.story_router import route_and_run
from reel_af.agents.tts_continuous import generate_continuous_audio, voice_for_tone
from reel_af.agents.video_gen import generate_videos
from reel_af.agents.visual_vocab import build_vocabulary
from reel_af.assembly.ffmpeg_stitch_v2 import stitch_v2
from reel_af.models import AngleProposal, Beat, Storyboard

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

app = typer.Typer(
    name="reel-af",
    help="URL → vertical reel via DeepSeek V4 Pro + Veo + Kokoro.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(stderr=True)


def _build_agent() -> Agent:
    model = os.environ.get("REEL_AF_ANGLE_MODEL", "openrouter/deepseek/deepseek-v4-pro")
    return Agent(
        node_id="reel-af",
        version="0.3.0",
        ai_config=AIConfig(
            model=model,
            api_key=os.environ["OPENROUTER_API_KEY"],
            api_base="https://openrouter.ai/api/v1",
        ),
    )


async def _run(url: str, out_dir: Path, run_id: str) -> None:
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY not set (put it in .env)")
    timings: dict[str, float] = {}
    t_total = time.time()
    af = _build_agent()

    # ── 1. Navigate ────────────────────────────────────────────────
    console.print(f"[bold cyan]1.[/bold cyan] Reading source: {url}")
    t = time.time()
    source = await navigate(af, url)
    timings["navigate"] = time.time() - t
    console.print(
        f"   [green]✓[/green] {source.title!r}  ({len(source.key_claims)} claims, "
        f"surprise={source.surprise_score}/10)"
    )

    # ── 2. Distiller — faithful structured summary (NO creativity) ─
    console.print("[bold cyan]2.[/bold cyan] Distilling article (faithful summary)…")
    t = time.time()
    summary = await distill(af, source)
    timings["distill"] = time.time() - t
    console.print(f"   [green]✓[/green] thesis: [italic]{summary.one_line_thesis}[/italic]")
    console.print(f"   domain: [bold]{summary.domain}[/bold]  takeaway: {summary.intended_takeaway}")

    # ── 3. Story Router — picks I or F based on article direction ──
    console.print("[bold cyan]3.[/bold cyan] Story router (picks I or F by direction)…")
    t = time.time()
    routed = await route_and_run(af, summary)
    timings["story"] = time.time() - t
    draft = routed.draft

    console.print(
        f"   [green]✓[/green] direction=[bold]{draft.direction}[/bold] → "
        f"arch=[bold]{routed.chosen_arch}[/bold] "
        f"([italic]{routed.arch_output.arch_name}[/italic]) "
        f"self={routed.arch_output.self_score:.1f}"
    )
    console.print(
        f"   tricks: hook=[yellow]{draft.hook_trick}[/yellow]  "
        f"retention=[yellow]{draft.retention_trick}[/yellow]  "
        f"close=[yellow]{draft.close_trick}[/yellow]  "
        f"tone=[yellow]{draft.voice_tone}[/yellow]"
    )
    wc = len(draft.script.split())
    console.print(f"   {wc} words / ~{wc/2.6:.1f}s")
    console.print(f"   [dim]{draft.script}[/dim]")

    if routed.arch_output.self_score < 7:
        console.print(
            f"   [bold red]⚠ self-score={routed.arch_output.self_score:.1f}/10[/bold red] — "
            f"this article may not be reel-worthy. Generating anyway."
        )

    # ── 4. Scene Breaker (one .ai picks both breaks + captions) ───
    console.print("[bold cyan]4.[/bold cyan] Breaking into scenes + captions…")
    t = time.time()
    scenes = await break_scenes(af, draft.script)
    timings["scenes"] = time.time() - t
    for s in scenes:
        console.print(
            f"   [{s.idx}] cap=[bold]{s.caption!r}[/bold]  · {s.sentence!r}"
        )

    # ── 5. Visual vocabulary (article-specific motifs) ─────────────
    console.print("[bold cyan]5.[/bold cyan] Building visual vocabulary (article-specific)…")
    t = time.time()
    vocab = await build_vocabulary(af, summary)
    timings["vocab"] = time.time() - t
    console.print(f"   [green]✓[/green] {len(vocab.motifs)} motifs:")
    for m in vocab.motifs:
        console.print(f"   - [yellow]{m.motif_id}[/yellow]: {m.description[:90]}")

    # ── 6 + 6a. Shot Director ∥ Continuous TTS (no dep — start both) ──
    console.print(
        "[bold cyan]6.[/bold cyan] Shot director (vocab-grounded) + continuous TTS in parallel…"
    )
    media_dir = out_dir / "media"
    voice = voice_for_tone(draft.voice_tone)
    console.print(f"   voice: [bold]{voice}[/bold]  ({draft.voice_tone})")
    t = time.time()
    plans_task = asyncio.create_task(
        direct_shots_v2(af, scenes, tone=draft.voice_tone, full_script=draft.script, vocab=vocab)
    )
    tts_task = asyncio.create_task(
        generate_continuous_audio(
            full_script=draft.script,
            scenes=scenes,
            voice=voice,
            out_dir=media_dir,
            tone=draft.voice_tone,
        )
    )
    plans, (audio_artifacts, full_audio) = await asyncio.gather(plans_task, tts_task)
    timings["director+tts"] = time.time() - t
    console.print(
        f"   [green]✓[/green] {len(plans)} shot plans + ONE continuous {full_audio.name} "
        f"split into {len(audio_artifacts)} per-scene WAVs"
    )
    for s, p in zip(scenes, plans):
        console.print(
            f"   [{s.idx}] [yellow]{p.visual_trick:20s}[/yellow] "
            f"({p.anchor_type:8s}) → {p.image_prompt[:80]}"
        )

    # ── 6. Video gen (grok-imagine → Veo i2v per scene, parallel) ─
    console.print("[bold cyan]6.[/bold cyan] Generating videos (Veo i2v) per scene…")
    t = time.time()
    video_artifacts = await generate_videos(scenes, plans, media_dir)
    timings["video"] = time.time() - t
    console.print(f"   [green]✓[/green] {len(video_artifacts)} videos")

    # ── 7. Assembly (per-segment renders parallel + fast concat) ──
    console.print("[bold cyan]7.[/bold cyan] Stitching with ffmpeg (parallel renders)…")
    t = time.time()
    # Shape a Storyboard for ReelResult back-compat.
    sb = Storyboard(
        angle=AngleProposal(
            frame="pattern_interrupt",
            hook_line=draft.script.split(".")[0],
            angle=draft.script,
            why_works=(
                f"arch={routed.chosen_arch} direction={draft.direction} "
                f"hook={draft.hook_trick} retention={draft.retention_trick} "
                f"close={draft.close_trick}"
            ),
            predicted_score=draft.viral_score,
        ),
        beats=[
            Beat(
                idx=s.idx,
                duration_s=s.est_duration_s,
                image_prompt=p.image_prompt,
                caption=s.caption,
                vo_line=s.sentence,
                motion_hint="static",
            )
            for s, p in zip(scenes, plans)
        ],
        total_duration_s=sum(s.est_duration_s for s in scenes),
        style_notes="",
    )
    result = await stitch_v2(
        segments=scenes,
        plans=plans,
        video_artifacts=video_artifacts,
        audio_artifacts=audio_artifacts,
        out_dir=out_dir,
        run_id=run_id,
        storyboard_for_result=sb,
    )
    timings["stitch"] = time.time() - t

    # ── Summary ───────────────────────────────────────────────────
    total = time.time() - t_total
    table = Table(title="Timing", show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim")
    table.add_column(justify="right")
    for k, v in timings.items():
        table.add_row(k, f"{v:.1f}s")
    table.add_row("[bold]total", f"[bold]{total:.1f}s")
    console.print(Panel(table, title="reel-af", border_style="cyan"))

    import sys
    sys.__stdout__.write(str(result.output_path) + "\n")
    sys.__stdout__.flush()


@app.command(name="script")
def script_cmd(
    url: Annotated[str, typer.Argument(help="Article URL.")],
) -> None:
    """Generate ONLY the script (no Veo, no images, no audio) — cheap iteration.

    Runs navigate → distill → router → return draft. Takes ~60-90s and
    costs cents. Perfect for tuning writer prompts before burning Veo.
    """
    from reel_af.agents.distiller import distill
    from reel_af.agents.navigator import navigate
    from reel_af.agents.story_router import route_and_run

    async def _script() -> None:
        af = _build_agent()
        console.print(f"[cyan]navigate[/cyan] {url}")
        t = time.time()
        source = await navigate(af, url)
        console.print(f"  [green]✓[/green] {time.time()-t:.1f}s — {source.title!r}")

        console.print("[cyan]distill[/cyan]")
        t = time.time()
        summary = await distill(af, source)
        console.print(
            f"  [green]✓[/green] {time.time()-t:.1f}s — "
            f"domain=[bold]{summary.domain}[/bold] "
            f"familiarity=[bold]{summary.topic_familiarity}[/bold]"
        )
        console.print(f"  thesis: [italic]{summary.one_line_thesis}[/italic]")

        console.print("[cyan]story router[/cyan] (picks I or F, runs full script loop)")
        t = time.time()
        routed = await route_and_run(af, summary)
        console.print(
            f"  [green]✓[/green] {time.time()-t:.1f}s — "
            f"direction=[bold]{routed.draft.direction}[/bold] "
            f"arch=[bold]{routed.chosen_arch}[/bold] "
            f"score={routed.arch_output.self_score:.1f}"
        )
        console.print(
            f"  tricks: hook=[yellow]{routed.draft.hook_trick}[/yellow] "
            f"retention=[yellow]{routed.draft.retention_trick}[/yellow] "
            f"close=[yellow]{routed.draft.close_trick}[/yellow]"
        )
        wc = len(routed.draft.script.split())
        console.print(f"  words: {wc} (~{wc/2.6:.1f}s spoken)")
        console.print()
        console.print("[bold]SCRIPT:[/bold]")
        console.print(f"  {routed.draft.script}")

    asyncio.run(_script())


@app.command(name="stories")
def stories_cmd(
    url: Annotated[str, typer.Argument(help="Article URL.")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output dir. Default: output/stories-<run>/"),
    ] = None,
) -> None:
    """Run ALL story architectures on one article and save side-by-side markdown.

    Compare architectures (A: pool+critic, B: hook-first, E: reverse, H: tournament)
    without burning Veo dollars on video generation. Outputs stories.md.
    """
    from reel_af.stories import render_markdown, run_all

    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY not set (put it in .env)")
    run_id = uuid.uuid4().hex[:8]
    out_dir = out or (Path.cwd() / "output" / f"stories-{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    af = _build_agent()
    console.print("[bold cyan]stories[/bold cyan] running 4 architectures in parallel on:")
    console.print(f"  {url}")
    summary, outputs = asyncio.run(run_all(af, url))

    md = render_markdown(url, summary, outputs)
    out_path = out_dir / "stories.md"
    out_path.write_text(md, encoding="utf-8")

    # Terminal summary.
    console.print()
    console.print(
        f"[bold]{summary.domain}[/bold] — {summary.one_line_thesis}"
    )
    console.print()
    for o in outputs:
        if o.draft is None:
            console.print(f"  [red]✗[/red] [{o.arch_id}] {o.arch_name} — failed")
            continue
        console.print(
            f"  [{o.arch_id}] [bold]{o.arch_name}[/bold]  "
            f"self={o.self_score:.1f}  wall={o.wall_time_s:.0f}s  "
            f"words={o.word_count()}"
        )
        console.print(f"      [dim]{o.draft.script}[/dim]")
        console.print()

    import sys as _sys
    _sys.__stdout__.write(str(out_path) + "\n")
    _sys.__stdout__.flush()


async def _run_v2(url: str, out_dir: Path, run_id: str) -> None:
    """v2 pipeline driver — thin wrapper around run_pipeline_v2."""
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY not set (put it in .env)")
    from reel_af.v2.pipeline import run_pipeline_v2

    af = _build_agent()
    console.print(Panel.fit(f"[bold]reel-af v2[/bold]  URL: {url}", border_style="cyan"))
    result = await run_pipeline_v2(af, url, out_dir, run_id)
    console.print(
        f"[green]✓[/green] reel ready: [bold]{result['video_path']}[/bold] "
        f"({result['duration_s']:.1f}s, {result['shot_count']} shots, "
        f"{result['card_count']} cards, {result['accent_count']} accents, "
        f"hook={result['hook_variant']}, mode={result['content_mode']})"
    )
    tbl = Table(title="phase timings (v2)")
    tbl.add_column("phase")
    tbl.add_column("seconds", justify="right")
    for k, v in result["timings"].items():
        tbl.add_row(k, f"{v}")
    tbl.add_row("[bold]wall[/bold]", f"[bold]{result['wall_time_s']}[/bold]")
    console.print(tbl)


@app.command(name="generate")
def generate_cmd(
    url: Annotated[str, typer.Argument(help="Article / page URL to turn into a reel.")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output directory. Default: output/<run_id>/"),
    ] = None,
    algo: Annotated[
        str,
        typer.Option("--algo", help="Pipeline version: v1 (current default) or v2 (new — see docs/ARCHITECTURE_V2.md)."),
    ] = "v1",
) -> None:
    """Generate a vertical reel from a single URL."""
    run_id = uuid.uuid4().hex[:8]
    out_dir = out or (Path.cwd() / "output" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    if algo == "v2":
        asyncio.run(_run_v2(url, out_dir, run_id))
    elif algo == "v1":
        asyncio.run(_run(url, out_dir, run_id))
    else:
        raise SystemExit(f"unknown --algo {algo!r}; use 'v1' or 'v2'")


if __name__ == "__main__":
    app()
