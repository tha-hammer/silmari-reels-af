"""reel-af CLI — turn a URL or a topic into a vertical reel.

Two subcommands mirror the two entry reasoners:

  reel-af article URL                # article_to_reel
  reel-af topic "topic phrase"       # topic_to_reel

The CLI submits async executions to the AgentField control plane and
polls until the run finishes. Start the stack with ``docker compose up
--build`` or run ``af server`` + ``reel-af serve`` before invoking it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Optional

import aiohttp
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

app = typer.Typer(
    name="reel-af",
    help="URL or topic → vertical reel (OpenRouter only).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(stderr=True)
DEFAULT_AGENTFIELD_SERVER = os.getenv("AGENTFIELD_SERVER", "http://localhost:8080")
DEFAULT_TIMEOUT_S = 1800
DEFAULT_POLL_INTERVAL_S = 5.0


def _require_key() -> None:
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY not set (put it in .env)")


def _server_url(server: str) -> str:
    return server.rstrip("/")


def _decode_json(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"AgentField response was not JSON: {body[:500]}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"AgentField response was not an object: {data!r}")
    return data


async def _execute_reasoner(
    *,
    server: str,
    target: str,
    input_payload: dict[str, Any],
    timeout_s: int,
    poll_interval_s: float,
) -> tuple[str, dict[str, Any]]:
    base = _server_url(server)
    execute_url = f"{base}/api/v1/execute/async/{target}"
    timeout = aiohttp.ClientTimeout(total=None)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(
                execute_url,
                json={"input": input_payload},
            ) as resp:
                body = await resp.text()
        except aiohttp.ClientError as exc:
            raise SystemExit(
                f"Cannot reach AgentField at {base}. Start it with "
                "`docker compose up --build` or pass --server."
            ) from exc

        if resp.status >= 400:
            raise SystemExit(
                f"AgentField rejected {target} ({resp.status}): {body[:800]}"
            )

        started = _decode_json(body)
        exec_id = started.get("execution_id") or started.get("id")
        if not exec_id:
            raise SystemExit(f"AgentField did not return execution_id: {started}")

        console.print(f"[dim]execution_id: {exec_id}[/dim]")
        start = time.monotonic()
        last_status: str | None = None

        while True:
            elapsed = int(time.monotonic() - start)
            if elapsed > timeout_s:
                raise SystemExit(
                    f"Timed out after {timeout_s}s waiting for {exec_id}."
                )

            try:
                async with session.get(f"{base}/api/v1/executions/{exec_id}") as resp:
                    body = await resp.text()
            except aiohttp.ClientError as exc:
                console.print(f"[yellow]poll error:[/yellow] {exc}")
                await asyncio.sleep(poll_interval_s)
                continue

            if resp.status >= 400:
                raise SystemExit(
                    f"AgentField poll failed for {exec_id} ({resp.status}): "
                    f"{body[:800]}"
                )

            state = _decode_json(body)
            status = str(state.get("status") or "?")
            if status != last_status or elapsed % 60 == 0:
                console.print(f"[dim][{elapsed:4d}s] status={status}[/dim]")
                last_status = status

            if status == "succeeded":
                result = state.get("result")
                if not isinstance(result, dict):
                    raise SystemExit(
                        f"Execution {exec_id} succeeded without a JSON result: {state}"
                    )
                return str(exec_id), result

            if status in {"failed", "cancelled", "canceled"}:
                raise SystemExit(
                    f"Execution {exec_id} {status}:\n"
                    f"{json.dumps(state, indent=2, default=str)[:4000]}"
                )

            await asyncio.sleep(poll_interval_s)


def _sidecar_dir(result: dict[str, Any], explicit_out_dir: Path | None) -> Path:
    if explicit_out_dir is not None:
        return explicit_out_dir

    video_path = result.get("video_path")
    if isinstance(video_path, str) and video_path:
        path = Path(video_path)
        if path.is_absolute():
            parts = path.parts
            if "output" in parts:
                output_idx = parts.index("output")
                return Path.cwd().joinpath(*parts[output_idx:]).parent
        return (Path.cwd() / path).parent

    run_id = str(result.get("run_id") or "unknown")
    return Path.cwd() / "output" / run_id


def _summarize(result: dict, run_id: str, out_path: Path) -> None:
    table = Table(title=f"reel-af run {run_id}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    for k in (
        "source", "video_path", "duration_s", "beat_count", "card_count",
        "accent_count", "hook", "tease", "topic", "url",
        "content_mode", "domain", "voice_id",
    ):
        if k in result and result[k] is not None:
            table.add_row(k, str(result[k]))
    console.print(table)
    if "timings_s" in result:
        console.print(
            Panel(
                "\n".join(
                    f"  {k:14s}{v:>6.1f}s"
                    for k, v in result["timings_s"].items()
                ),
                title="timings",
                border_style="dim",
            )
        )
    sidecar = out_path / "result.json"
    sidecar.write_text(json.dumps(result, indent=2, default=str))
    console.print(f"\n[green]→ {result.get('video_path')}[/green]")
    console.print(f"[dim]   result.json → {sidecar}[/dim]")


@app.command("article")
def article(
    url: Annotated[str, typer.Argument(help="The article URL.")],
    out_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--out",
            help="Output directory as seen by the running agent.",
            show_default=False,
        ),
    ] = None,
    server: Annotated[
        str,
        typer.Option("--server", help="AgentField control-plane URL."),
    ] = DEFAULT_AGENTFIELD_SERVER,
    timeout_s: Annotated[
        int,
        typer.Option("--timeout", help="Maximum seconds to wait."),
    ] = DEFAULT_TIMEOUT_S,
    poll_interval_s: Annotated[
        float,
        typer.Option("--poll-interval", help="Seconds between status polls."),
    ] = DEFAULT_POLL_INTERVAL_S,
) -> None:
    """Turn an article URL into a vertical viral reel."""
    input_payload: dict[str, Any] = {"url": url}
    if out_dir is not None:
        input_payload["out_dir"] = str(out_dir)

    console.rule("[bold]article_to_reel")
    console.print(f"  url: [cyan]{url}[/cyan]")
    console.print(f"  server: [dim]{_server_url(server)}[/dim]")
    if out_dir is not None:
        console.print(f"  out: [dim]{out_dir}[/dim]")
    console.print()

    exec_id, result = asyncio.run(
        _execute_reasoner(
            server=server,
            target="reel-af.reel_article_to_reel",
            input_payload=input_payload,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
    )
    if "error" in result:
        console.print(f"[red]error:[/red] {result['error']}")
        sys.exit(1)
    run_id = str(result.get("run_id") or exec_id[:8])
    out_path = _sidecar_dir(result, out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    _summarize(result, run_id, out_path)


@app.command("topic")
def topic(
    topic: Annotated[str, typer.Argument(help="The topic phrase.")],
    out_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--out",
            help="Output directory as seen by the running agent.",
            show_default=False,
        ),
    ] = None,
    server: Annotated[
        str,
        typer.Option("--server", help="AgentField control-plane URL."),
    ] = DEFAULT_AGENTFIELD_SERVER,
    timeout_s: Annotated[
        int,
        typer.Option("--timeout", help="Maximum seconds to wait."),
    ] = DEFAULT_TIMEOUT_S,
    poll_interval_s: Annotated[
        float,
        typer.Option("--poll-interval", help="Seconds between status polls."),
    ] = DEFAULT_POLL_INTERVAL_S,
) -> None:
    """Turn a topic into a vertical viral reel (multi-reasoner cascade)."""
    input_payload: dict[str, Any] = {"topic": topic}
    if out_dir is not None:
        input_payload["out_dir"] = str(out_dir)

    console.rule("[bold]topic_to_reel")
    console.print(f"  topic: [cyan]{topic}[/cyan]")
    console.print(f"  server: [dim]{_server_url(server)}[/dim]")
    if out_dir is not None:
        console.print(f"  out:   [dim]{out_dir}[/dim]")
    console.print()

    exec_id, result = asyncio.run(
        _execute_reasoner(
            server=server,
            target="reel-af.reel_topic_to_reel",
            input_payload=input_payload,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
    )
    if "error" in result:
        console.print(f"[red]error:[/red] {result['error']}")
        sys.exit(1)
    run_id = str(result.get("run_id") or exec_id[:8])
    out_path = _sidecar_dir(result, out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    _summarize(result, run_id, out_path)


def _composite_text_provider() -> Any:
    """The AgentField ``Agent`` (exposes ``.ai``) — drives hook + image moments."""
    from reel_af.app import app

    return app


def _composite_image_provider() -> Any:
    """The media ``OpenRouterProvider`` — drives image generation."""
    from agentfield.media_providers import OpenRouterProvider

    return OpenRouterProvider()


@app.command("composite")
def composite(
    url: Annotated[str, typer.Argument(help="The source video URL (e.g. a YouTube link).")],
    out_dir: Annotated[
        Optional[Path],
        typer.Option("--out", help="Output directory for the reel.", show_default=False),
    ] = None,
    fast: Annotated[
        bool,
        typer.Option(
            "--fast/--rich",
            help="Fast = plain stitched reel (no banner/captions/cut-ins). "
            "Rich (default) burns the full finish.",
        ),
    ] = False,
) -> None:
    """URL → crisp reel with banner + captions + image cut-ins (the default finish).

    ``--fast`` opts out and yields the plain stitched reel.
    """
    from reel_af.render.composite_pipeline import composite_to_reel

    _require_key()
    work = out_dir or (_PROJECT_ROOT / "out" / "composite")

    console.rule("[bold]composite_to_reel")
    console.print(f"  url:  [cyan]{url}[/cyan]")
    console.print(f"  mode: [dim]{'fast (raw stitched)' if fast else 'rich (banner+captions+cut-ins)'}[/dim]")
    console.print(f"  out:  [dim]{work}[/dim]")
    console.print()

    text_provider = None if fast else _composite_text_provider()
    image_provider = None if fast else _composite_image_provider()
    final = asyncio.run(
        composite_to_reel(
            url,
            work,
            text_provider=text_provider,
            image_provider=image_provider,
            raw=fast,
        )
    )
    console.print(f"[green]done:[/green] {final}")


def _ffprobe_duration(source: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _resolve_source(source: str, work: Path) -> Path:
    """A local path is used as-is; a URL is downloaded to ``work/source.mp4``."""
    if source.startswith(("http://", "https://")):
        from reel_af.render.hooks import download_crisp_source

        dest = work / "source.mp4"
        console.print(f"  [dim]downloading {source} → {dest}[/dim]")
        return download_crisp_source(source, dest)
    path = Path(source).expanduser()
    if not path.exists():
        raise SystemExit(f"source not found: {path}")
    return path


def _resolve_words(source: Path, whisper_json: Path | None, work: Path) -> list:
    """Word timestamps for the source: a cached whisper JSON if given, else run
    the repo's whisper helper once (its result stays consumable as tuples)."""
    from reel_af.render.middle_third import load_whisper_words

    if whisper_json is not None:
        console.print(f"  [dim]using cached transcript {whisper_json}[/dim]")
        return load_whisper_words(whisper_json)
    from reel_af.render.captions import caption_words

    console.print("  [dim]transcribing source (whisper)…[/dim]")
    return caption_words(source, workdir=work)


@app.command("reels")
def reels(
    source: Annotated[str, typer.Argument(help="Source video: a local file path or a URL.")],
    preset: Annotated[
        str,
        typer.Option("--preset", help="Named reel-format preset (see config/presets.json)."),
    ],
    out_dir: Annotated[
        Optional[Path],
        typer.Option("--out", help="Output directory for the reels.", show_default=False),
    ] = None,
    only: Annotated[
        Optional[str],
        typer.Option("--only", help="Comma-separated 1-based reel indices (default: all)."),
    ] = None,
    whisper_json: Annotated[
        Optional[Path],
        typer.Option("--whisper", help="Cached whisper word-timestamp JSON (skips transcription)."),
    ] = None,
    chrome: Annotated[
        str,
        typer.Option("--chrome", help="Chromium/Chrome executable for Remotion."),
    ] = "/usr/bin/chromium",
) -> None:
    """Cut a source into preset-formatted reels with a Remotion overlay.

    Currently supports presets whose ``overlay`` is ``middle_third`` (the
    ``middle-third-dynamic`` format): each reel is a window of the source with a
    script-synced ``MiddleThird`` overlay composited on top.
    """
    import shutil

    from reel_af.render import middle_third
    from reel_af.render.presets import load_preset, preset_names

    try:
        cfg = load_preset(preset)
    except KeyError:
        raise SystemExit(f"unknown preset {preset!r}; available: {preset_names()}")

    overlay = cfg.get("overlay")
    if overlay != "middle_third":
        raise SystemExit(
            f"preset {preset!r} uses overlay={overlay!r}; `reels` currently supports "
            "overlay='middle_third'. Use `composite` for other formats."
        )

    fps = int(cfg.get("fps", 30))
    reel_s = float(cfg["reel_seconds"])
    work = out_dir or (_PROJECT_ROOT / "out" / "reels" / preset)
    work.mkdir(parents=True, exist_ok=True)

    console.rule(f"[bold]reels · {preset}")
    src = _resolve_source(source, work)
    words = _resolve_words(src, whisper_json, work)
    total = _ffprobe_duration(src)
    n = int(total // reel_s)
    tail = total - n * reel_s
    console.print(f"  source={total:.0f}s → {n} × {reel_s:.0f}s reels; words={len(words)}")
    if tail >= 1:
        console.print(f"  [dim](final {tail:.0f}s < one reel — not cut)[/dim]")
    if n < 1:
        raise SystemExit(
            f"source is {total:.0f}s — shorter than one {reel_s:.0f}s reel; nothing to cut."
        )

    if only:
        try:
            picks = [int(x) for x in only.split(",")]
        except ValueError:
            raise SystemExit(f"--only must be comma-separated integers, got {only!r}")
        out_of_range = [i for i in picks if i < 1 or i > n]
        if out_of_range:
            raise SystemExit(f"--only index out of range (valid: 1..{n}): {out_of_range}")
    else:
        picks = list(range(1, n + 1))
    for idx in picks:
        t0 = (idx - 1) * reel_s
        d = work / f"reel{idx:02d}"
        seq = d / "seq"
        segs = middle_third.window_segments(words, t0, t0 + reel_s, cfg, fps=fps)
        total_frames = int(reel_s * fps)
        console.print(f"  [dim]reel{idx:02d} [{t0:.0f}-{t0 + reel_s:.0f}s] segs={len(segs)}[/dim]")
        middle_third.render_overlay(segs, total_frames, seq, cfg, chrome=chrome)
        final = middle_third.composite_window(src, t0, reel_s, seq, d / f"reel{idx:02d}.mp4", fps=fps)
        shutil.rmtree(seq, ignore_errors=True)
        console.print(f"  [green]→ {final}[/green]")

    console.print(f"[green]done:[/green] {work}")


@app.command("serve")
def serve() -> None:
    """Run the AgentField node so the reasoners register with the control plane."""
    _require_key()
    from reel_af.app import main as _main
    _main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
