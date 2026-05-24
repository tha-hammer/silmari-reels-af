"""Reel pipeline — same logic for the CLI and the AgentField reasoner.

Pulled out of cli.py so both entrypoints share one implementation:
  - `reel-af generate URL` (local CLI for dev)
  - `curl POST /api/v1/execute/reel-af.reel_generate {url}` (AgentField workflow)

Caller passes an `app` (AgentField Agent instance) for all `.ai()` calls.
Returns a dict with the artifacts the caller / API can serialise.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

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


async def run_pipeline(app: Any, url: str, out_dir: Path, run_id: str) -> dict:
    """Run the full URL → reel pipeline. Returns serialisable artifacts."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    t_total = time.time()

    # ───────────────────────────────────────────────────────────────
    # Pipeline parallelism (mirrors the app.py workflow DAG):
    #   navigate → distill
    #     ↓
    #     ├── story_router (compose) ──┐
    #     └── build_vocabulary       *─┤   (vocab only needs summary)
    #                                  ↓
    #                               break_scenes
    #                                  ↓
    #     ┌── direct_shots_v2 ─── continuous_tts *  (audio only needs script+scenes)
    #     ↓
    #     generate_videos        ← audio still streaming
    #     ↓
    #     await audio
    #     ↓
    #     stitch
    # ───────────────────────────────────────────────────────────────
    media_dir = out_dir / "media"

    # 1. Navigate (serial root).
    t = time.time()
    source = await navigate(app, url)
    timings["navigate"] = time.time() - t

    # 2. Distill (serial — needs source).
    t = time.time()
    summary = await distill(app, source)
    timings["distill"] = time.time() - t

    # 3. Story router ∥ vocabulary — both only need summary.
    t = time.time()
    story_task = asyncio.create_task(route_and_run(app, summary))
    vocab_task = asyncio.create_task(build_vocabulary(app, summary))
    routed = await story_task
    timings["story"] = time.time() - t
    draft = routed.draft
    voice = voice_for_tone(draft.voice_tone)

    # 4. Scene breaker (serial — needs script).
    t = time.time()
    scenes = await break_scenes(app, draft.script)
    timings["scenes"] = time.time() - t

    # Vocabulary almost always done by now.
    vocab = await vocab_task
    timings["vocab_wait"] = round(time.time() - t, 2)

    # 5. Shot director ∥ continuous TTS — independent past this point.
    t = time.time()
    plans_task = asyncio.create_task(
        direct_shots_v2(
            app, scenes,
            tone=draft.voice_tone, full_script=draft.script, vocab=vocab,
            topic_familiarity=summary.topic_familiarity,
            content_mode=summary.content_mode,
        )
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
    plans = await plans_task
    timings["director"] = time.time() - t

    # 6. Veo i2v per scene (parallel). Audio still streaming.
    t = time.time()
    video_artifacts = await generate_videos(scenes, plans, media_dir)
    timings["video"] = time.time() - t

    # Audio almost certainly done by now.
    t = time.time()
    audio_artifacts, full_audio = await tts_task
    timings["audio_wait"] = round(time.time() - t, 2)

    # 8. Stitch (per-segment ffmpeg renders parallel + concat)
    t = time.time()
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

    return {
        "video_path": str(result.output_path),
        "duration_s": result.duration_s,
        "script": draft.script,
        "direction": draft.direction,
        "voice_tone": draft.voice_tone,
        "voice_id": voice,
        "tricks": {
            "hook": draft.hook_trick,
            "retention": draft.retention_trick,
            "close": draft.close_trick,
        },
        "self_score": routed.arch_output.self_score,
        "chosen_arch": routed.chosen_arch,
        "captions": [s.caption for s in scenes],
        "scene_sentences": [s.sentence for s in scenes],
        "motifs": [m.motif_id for m in vocab.motifs],
        "content_mode": summary.content_mode,
        "topic_familiarity": summary.topic_familiarity,
        "audience_level": summary.audience_level,
        "run_id": run_id,
        "timings": {k: round(v, 1) for k, v in timings.items()},
        "wall_time_s": round(time.time() - t_total, 1),
    }
