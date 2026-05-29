"""reel-af v2 orchestrator — URL → vertical reel in 6 phases.

Replaces the v1 router + multi-architecture + multi-stage pipeline with a
single Hook → Mechanism → Payoff → Loop structure parameterized by content_mode.

Phases:
  1. extract_essence (.harness)        — navigate + distill collapsed
  2. compose_script (.ai)              — one call, fixed structure
  3. generate_tts (Gemini)             — audio + per-word timings
  4. pack_cards + group_into_shots     — DETERMINISTIC, no LLM
  5. plan_visuals ∥ plan_accents       — parallel .ai per shot
  6. images → video → stitch           — fan-out + per-shot ffmpeg

Returns the same dict shape as v1's pipeline.run_pipeline so the CLI can
swap them with --algo v2 with zero adapter code.

Reference: docs/ARCHITECTURE_V2.md
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from reel_af.v2.agents.accent import plan_accents
from reel_af.v2.agents.compose import compose_script
from reel_af.v2.agents.extract import extract_essence
from reel_af.v2.agents.visual import plan_visuals
from reel_af.v2.models import ReelV2Result
from reel_af.v2.planning.shot_planner import group_into_shots, pack_cards
from reel_af.v2.render.alignment import align_audio
from reel_af.v2.render.images import (
    gen_first_frame_v2,  # noqa: F401 — imported eagerly so SDK patches load
)
from reel_af.v2.render.stitch import stitch_v2
from reel_af.v2.render.tts import _strip_tts_tags, synthesize_audio, voice_for_tone
from reel_af.v2.render.video import generate_videos


async def run_pipeline_v2(
    app: Any,
    url: str,
    out_dir: Path,
    run_id: str,
) -> dict:
    """Run the URL → reel pipeline. Returns a dict with the artifacts so the
    CLI / API can serialise the result. Shape mirrors v1's run_pipeline.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir = out_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    t_total = time.time()

    # ─── Phase 1: extract essence ────────────────────────────────────
    t = time.time()
    essence = await extract_essence(app, url)
    timings["extract"] = time.time() - t

    # ─── Phase 2: compose script ─────────────────────────────────────
    t = time.time()
    script = await compose_script(app, essence)
    timings["compose"] = time.time() - t

    # ─── Phase 3: TTS — synthesize audio only ───────────────────────
    t = time.time()
    voice = voice_for_tone(getattr(script, "voice_tone", "wonder"))
    spoken_narration = _strip_tts_tags(script.narration)
    full_audio_path, _duration_s = await synthesize_audio(
        narration=script.narration,
        voice=voice,
        out_dir=media_dir,
    )
    timings["tts"] = time.time() - t

    # ─── Phase 4: forced alignment — real per-word timestamps ──────
    t = time.time()
    word_timings = await align_audio(full_audio_path, spoken_narration)
    timings["align"] = time.time() - t

    # ─── Phase 5: plan shots (deterministic) ─────────────────────────
    t = time.time()
    cards = pack_cards(word_timings)
    shots = group_into_shots(cards)
    if not shots:
        raise RuntimeError(
            "shot_planner produced zero shots — check TTS word timings"
        )
    timings["plan"] = time.time() - t

    # ─── Phase 6: visual ∥ accent (parallel per shot) ───────────────
    t = time.time()
    visuals_task = asyncio.create_task(
        plan_visuals(app, shots, essence, full_narration=spoken_narration)
    )
    accents_task = asyncio.create_task(plan_accents(app, shots, essence))
    visuals, accents = await asyncio.gather(visuals_task, accents_task)
    timings["plan_visual_accent"] = time.time() - t

    # ─── Phase 6: generate media + stitch ───────────────────────────
    t = time.time()
    artifacts = await generate_videos(
        shots=shots,
        visuals=visuals,
        out_dir=media_dir,
        content_mode=essence.content_mode,
    )
    timings["media"] = time.time() - t

    t = time.time()
    final_path = await stitch_v2(
        shots=shots,
        visuals=visuals,
        artifacts=artifacts,
        accents=accents,
        full_audio_path=full_audio_path,
        out_dir=out_dir,
        run_id=run_id,
    )
    timings["stitch"] = time.time() - t

    # Probe final duration for the result envelope.
    from reel_af.v2.render.tts import _probe_duration
    duration_s = _probe_duration(final_path)

    result = ReelV2Result(
        output_path=final_path,
        duration_s=duration_s,
        narration=spoken_narration,
        hook=script.hook,
        hook_variant=script.hook_variant,
        content_mode=essence.content_mode,
        target_wpm=script.target_wpm,
        domain=essence.domain,
        shot_count=len(shots),
        card_count=sum(len(s.cards) for s in shots),
        accent_count=sum(1 for a in accents if a is not None),
        run_id=run_id,
        timings={k: round(v, 1) for k, v in timings.items()},
        wall_time_s=round(time.time() - t_total, 1),
    )

    # Return a dict that matches v1's shape where it overlaps, plus v2-only
    # fields. The CLI / API can serialise either.
    return {
        "video_path": str(result.output_path),
        "duration_s": result.duration_s,
        "script": spoken_narration,
        "hook": result.hook,
        "hook_variant": result.hook_variant,
        "content_mode": result.content_mode,
        "target_wpm": result.target_wpm,
        "domain": result.domain,
        "shot_count": result.shot_count,
        "card_count": result.card_count,
        "accent_count": result.accent_count,
        "run_id": result.run_id,
        "timings": result.timings,
        "wall_time_s": result.wall_time_s,
        "algo": "v2",
    }
