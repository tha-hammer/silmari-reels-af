"""reel-af AgentField agent — proper reasoner DAG.

Architecture (each reasoner is one cognitive unit; entry orchestrates via
app.call() so the control plane sees the full workflow DAG):

  generate (entry)
     │
     ├─ extract_source       (URL → SourceContent: navigate + distill)
     ├─ compose_script       (SourceContent → ReelDraft: router + I/F arch)
     ├─ plan_scenes_visuals  (script → Scenes + Vocab + Visual Arc)
     │
     │   ┌── async parallel ──┐
     ├──┤ generate_shot_plans (per-scene shot prompts)
     │   └── synthesize_audio (continuous TTS + silence-split)
     │
     ├─ generate_videos      (grok-imagine → Veo i2v per scene, parallel)
     └─ assemble_final       (ffmpeg per-segment parallel + concat)

Run pattern (3 terminals):

  # 1. start the control plane
  af server

  # 2. start the reel-af agent (registers + listens on :8002)
  cd examples/reel-af
  uv run python -m reel_af.app

  # 3. ASYNC invoke — gets execution_id immediately, no 90s timeout
  EXEC=$(curl -sS -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_generate \\
    -H 'Content-Type: application/json' \\
    -d '{"input":{"url":"https://example.com/article"}}' | jq -r .execution_id)
  while :; do
    R=$(curl -sS http://localhost:8080/api/v1/executions/$EXEC)
    S=$(echo "$R" | jq -r .status)
    case "$S" in
      succeeded) echo "$R" | jq .result; break ;;
      failed)    echo "$R" | jq .;       break ;;
      *)         sleep 5 ;;
    esac
  done
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env BEFORE importing agentfield so API keys are present.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

from agentfield import Agent, AgentRouter, AIConfig  # noqa: E402

# Apply SDK bug-fixes (see AGENTFIELD_SDK_ISSUES.md) at startup so every
# call to OpenRouterProvider gets the fixed behaviour. Module is idempotent.
import reel_af.sdk_patches  # noqa: E402, F401


app = Agent(
    node_id=os.getenv("AGENT_NODE_ID", "reel-af"),
    agentfield_server=os.getenv("AGENTFIELD_SERVER", "http://localhost:8080"),
    version="0.1.0",
    description="URL → vertical viral reel via a multi-reasoner DAG.",
    ai_config=AIConfig(
        model=os.getenv("REEL_AF_ANGLE_MODEL", "openrouter/deepseek/deepseek-v4-pro"),
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        api_base="https://openrouter.ai/api/v1",
    ),
    dev_mode=True,
)

# All reel reasoners live under prefix "reel" → invoked as `reel-af.reel_<name>`.
reel = AgentRouter(prefix="reel", tags=["video", "viral"])


# ════════════════════════════════════════════════════════════════════
# PHASE REASONERS — each is one cognitive unit
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def extract_source(url: str) -> dict:
    """Phase 1 — navigate + distill: URL → structured article summary.

    Combines fetch + clean + AI-extracted claims + AI-distilled thesis.
    Returns the SourceContent and ArticleSummary as nested dicts.
    """
    from reel_af.agents.distiller import distill
    from reel_af.agents.navigator import navigate

    source = await navigate(app, url)
    summary = await distill(app, source)
    return {
        "source": source.model_dump(),
        "summary": summary.model_dump(),
    }


@reel.reasoner()
async def compose_script(summary: dict) -> dict:
    """Phase 2 — pick direction (router) + write script (arch I or F).

    The router selects whichever architecture historically wins on this
    article's direction; the chosen architecture writes the script.
    """
    from reel_af.agents.distiller import ArticleSummary
    from reel_af.agents.story_router import route_and_run

    article_summary = ArticleSummary(**summary)
    routed = await route_and_run(app, article_summary)
    return {
        "draft": routed.draft.model_dump(),
        "direction": routed.draft.direction,
        "chosen_arch": routed.chosen_arch,
        "self_score": routed.arch_output.self_score,
        "arch_trace": routed.arch_output.trace,
    }


@reel.reasoner()
async def break_scenes_step(script: str) -> dict:
    """Sub-reasoner: split the script into scenes + per-scene captions.

    Composable so the planner can call it via app.call() — control plane
    sees this as its own DAG node, enabling per-step retry/replay.
    """
    from reel_af.agents.scene_breaker import break_scenes
    scenes = await break_scenes(app, script)
    return {
        "scenes": [
            {"idx": s.idx, "sentence": s.sentence, "caption": s.caption,
             "est_duration_s": s.est_duration_s, "role": s.role}
            for s in scenes
        ]
    }


@reel.reasoner()
async def build_vocab_step(summary: dict) -> dict:
    """Sub-reasoner: build article-specific visual vocabulary motifs."""
    from reel_af.agents.distiller import ArticleSummary
    from reel_af.agents.visual_vocab import build_vocabulary
    article_summary = ArticleSummary(**summary)
    vocab = await build_vocabulary(app, article_summary)
    return {"vocabulary": vocab.model_dump()}


@reel.reasoner()
async def plan_visual_arc_step(
    scenes: list[dict], vocabulary: dict, tone: str, script: str,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> dict:
    """Sub-reasoner: assign anchor + visual_trick + motif per scene.

    Depends on BOTH break_scenes_step and build_vocab_step — the planner
    above fan-outs those two in parallel, then calls this one with both
    outputs. That's the depth-3 DAG: entry → planner → these subs.

    `topic_familiarity` and `content_mode` switch the visual planner into
    the right register (accessibility for obscure topics; scientific-
    technical for papers).
    """
    from reel_af.agents.scene_breaker import Scene
    from reel_af.agents.visual_arc import plan_visual_arc
    from reel_af.agents.visual_vocab import VisualVocabulary
    scene_objs = [Scene(**s) for s in scenes]
    vocab_obj = VisualVocabulary(**vocabulary)
    arc = await plan_visual_arc(
        app, scene_objs, tone=tone, full_script=script, vocab=vocab_obj,
        topic_familiarity=topic_familiarity,
        content_mode=content_mode,
    )
    return {"visual_arc": [p.model_dump() for p in arc]}


@reel.reasoner()
async def plan_scenes_visuals(
    summary: dict, script: str, tone: str = "wonder",
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> dict:
    """Phase 3 — composes 3 sub-reasoners via app.call() (depth-3 DAG).

    Fans out scene-breaking ∥ vocab-building (both depend only on inputs),
    then chains into arc-planning (depends on both outputs). The control
    plane records ALL of this as a workflow DAG you can inspect / replay.
    """
    node = app.node_id
    scenes_task = asyncio.create_task(
        app.call(f"{node}.reel_break_scenes_step", script=script)
    )
    vocab_task = asyncio.create_task(
        app.call(f"{node}.reel_build_vocab_step", summary=summary)
    )
    scenes_resp, vocab_resp = await asyncio.gather(scenes_task, vocab_task)
    arc_resp = await app.call(
        f"{node}.reel_plan_visual_arc_step",
        scenes=scenes_resp["scenes"], vocabulary=vocab_resp["vocabulary"],
        tone=tone, script=script, topic_familiarity=topic_familiarity,
        content_mode=content_mode,
    )
    return {
        "scenes": scenes_resp["scenes"],
        "vocabulary": vocab_resp["vocabulary"],
        "visual_arc": arc_resp["visual_arc"],
    }


@reel.reasoner()
async def generate_shot_plans(
    scenes: list[dict], visual_arc: list[dict], vocabulary: dict,
    tone: str, full_script: str,
    topic_familiarity: str = "hot",
    content_mode: str = "general",
) -> dict:
    """Phase 4a — per-scene shot plans (image prompt + motion prompt).

    Runs in parallel with synthesize_audio (no dependency between them).
    `topic_familiarity` and `content_mode` switch the shot director into
    the right register (obscure-accessibility or scientific-technical).
    """
    from reel_af.agents.scene_breaker import Scene
    from reel_af.agents.shot_director_v2 import _direct_one
    from reel_af.agents.visual_arc import SceneVisualPlan
    from reel_af.agents.visual_vocab import VisualVocabulary

    vocab = VisualVocabulary(**vocabulary)
    motif_by_id = {m.motif_id: m for m in vocab.motifs}
    arc_by_idx = {a["scene_idx"]: SceneVisualPlan(**a) for a in visual_arc}

    async def _one(scene_dict: dict):
        scene = Scene(**scene_dict)
        plan = arc_by_idx.get(scene.idx)
        if plan is None:
            # Arc planner skipped this scene_idx — fall back to a sensible
            # default anchored on the first vocabulary motif rather than
            # crashing the whole pipeline.
            plan = SceneVisualPlan(
                scene_idx=scene.idx,
                anchor_type="literal",
                visual_trick="isolated_object",
                motif_id=vocab.motifs[0].motif_id,
                one_line_concept=scene.sentence,
            )
        motif = motif_by_id.get(plan.motif_id, vocab.motifs[0])
        result = await _direct_one(
            app, scene, plan, tone, full_script,
            motif_description=motif.description,
            topic_familiarity=topic_familiarity,
            content_mode=content_mode,
        )
        return result.model_dump()

    plans = await asyncio.gather(*(_one(s) for s in scenes))
    return {"plans": plans}


@reel.reasoner()
async def synthesize_audio(
    full_script: str, scenes: list[dict], voice_tone: str, out_dir: str,
) -> dict:
    """Phase 4b — single continuous TTS call + sentence-boundary split.

    Generates the WHOLE script as one utterance (preserves prosody) then
    splits at silences for per-scene video alignment.
    """
    from reel_af.agents.scene_breaker import Scene
    from reel_af.agents.tts_continuous import generate_continuous_audio, voice_for_tone

    scene_objs = [Scene(**s) for s in scenes]
    voice = voice_for_tone(voice_tone)
    artifacts, full_audio = await generate_continuous_audio(
        full_script=full_script,
        scenes=scene_objs,
        voice=voice,
        out_dir=Path(out_dir),
        tone=voice_tone,
    )
    return {
        "voice": voice,
        "full_audio_path": str(full_audio),
        "segments": [
            {"idx": a.idx, "audio_path": str(a.audio_path)} for a in artifacts
        ],
    }


@reel.reasoner()
async def gen_first_frame_step(
    scene_idx: int, plan: dict, out_dir: str,
) -> dict:
    """Sub-reasoner: grok-imagine generates a vertical first frame from
    the shot plan's image_prompt. One call per scene → many in parallel."""
    from agentfield.media_providers import OpenRouterProvider
    from reel_af.agents.shot_director_v2 import ShotPlanV2
    from reel_af.agents.video_gen import _gen_first_frame

    provider = OpenRouterProvider()
    plan_obj = ShotPlanV2(**plan)
    frame_path = await _gen_first_frame(provider, plan_obj, scene_idx, Path(out_dir))
    return {"scene_idx": scene_idx, "frame_path": str(frame_path)}


@reel.reasoner()
async def gen_video_from_frame_step(
    scene_idx: int, plan: dict, scene: dict, frame_path: str, out_dir: str,
) -> dict:
    """Sub-reasoner: Veo image-to-video using a grok-imagine first frame.
    Falls back to ken-burns-on-still if Veo content-moderates the frame."""
    from agentfield.media_providers import OpenRouterProvider
    from reel_af.agents.scene_breaker import Scene
    from reel_af.agents.shot_director_v2 import ShotPlanV2
    from reel_af.agents.video_gen import _gen_video, _still_as_video

    provider = OpenRouterProvider()
    plan_obj = ShotPlanV2(**plan)
    scene_obj = Scene(**scene)
    try:
        video_path = await _gen_video(
            provider, plan_obj, scene_obj, Path(frame_path), Path(out_dir),
        )
    except Exception as e:
        # Veo content-moderation false-positive → fall back to still+ken-burns
        fallback = Path(out_dir) / f"seg-{scene_idx:02d}-fallback.mp4"
        video_path = await _still_as_video(Path(frame_path), 4.0, fallback)
    return {"scene_idx": scene_idx, "video_path": str(video_path), "used_fallback": False}


@reel.reasoner()
async def generate_videos_phase(
    scenes: list[dict], plans: list[dict], out_dir: str,
) -> dict:
    """Phase 5 — orchestrates per-scene (first_frame → veo) via app.call().

    Each scene is a 2-step chain (frame → video). All scenes run in
    parallel via asyncio.gather. The control plane records every node:
    `generate_videos_phase` → per-scene `gen_first_frame_step` then
    `gen_video_from_frame_step`. Depth-3 DAG inside this phase.
    """
    node = app.node_id

    async def _one_scene(scene_dict: dict, plan_dict: dict) -> dict:
        frame_resp = await app.call(
            f"{node}.reel_gen_first_frame_step",
            scene_idx=scene_dict["idx"], plan=plan_dict, out_dir=out_dir,
        )
        video_resp = await app.call(
            f"{node}.reel_gen_video_from_frame_step",
            scene_idx=scene_dict["idx"], plan=plan_dict, scene=scene_dict,
            frame_path=frame_resp["frame_path"], out_dir=out_dir,
        )
        return {"idx": scene_dict["idx"], "video_path": video_resp["video_path"]}

    videos = await asyncio.gather(
        *(_one_scene(s, p) for s, p in zip(scenes, plans))
    )
    return {"videos": list(videos)}


@reel.reasoner()
async def assemble_final(
    scenes: list[dict], plans: list[dict],
    video_segments: list[dict], audio_segments: list[dict],
    direction: str, hook_trick: str, retention_trick: str, close_trick: str,
    viral_score: int, out_dir: str, run_id: str,
) -> dict:
    """Phase 6 — concat per-segment ffmpeg renders into the final reel.mp4."""
    from reel_af.agents.scene_breaker import Scene
    from reel_af.agents.shot_director_v2 import ShotPlanV2
    from reel_af.assembly.ffmpeg_stitch_v2 import stitch_v2
    from reel_af.models import AngleProposal, Beat, BeatArtifact, Storyboard

    scene_objs = [Scene(**s) for s in scenes]
    plan_objs = [ShotPlanV2(**p) for p in plans]
    video_artifacts = [BeatArtifact(idx=v["idx"], image_path=Path(v["video_path"])) for v in video_segments]
    audio_artifacts = [BeatArtifact(idx=a["idx"], audio_path=Path(a["audio_path"])) for a in audio_segments]

    sb = Storyboard(
        angle=AngleProposal(
            frame="pattern_interrupt",
            hook_line=scene_objs[0].sentence,
            angle=" ".join(s.sentence for s in scene_objs),
            why_works=f"hook={hook_trick} retention={retention_trick} close={close_trick}",
            predicted_score=viral_score,
        ),
        beats=[
            Beat(
                idx=s.idx, duration_s=s.est_duration_s,
                image_prompt=p.image_prompt, caption=s.caption,
                vo_line=s.sentence, motion_hint="static",
            )
            for s, p in zip(scene_objs, plan_objs)
        ],
        total_duration_s=sum(s.est_duration_s for s in scene_objs),
        style_notes="",
    )
    result = await stitch_v2(
        segments=scene_objs, plans=plan_objs,
        video_artifacts=video_artifacts, audio_artifacts=audio_artifacts,
        out_dir=Path(out_dir), run_id=run_id, storyboard_for_result=sb,
    )
    return {
        "video_path": str(result.output_path),
        "duration_s": result.duration_s,
    }


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER — orchestrates the DAG via app.call()
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def generate(url: str, out_dir: str | None = None) -> dict:
    """Entry point — composes the reel via app.call() to each phase reasoner.

    Each app.call() goes through the control plane, so the workflow DAG is
    visible in the UI + you can rerun individual phases for debugging.

    Example:
      curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_generate \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"url":"https://example.com/article"}}'
    """
    if "OPENROUTER_API_KEY" not in os.environ:
        return {"error": "OPENROUTER_API_KEY not set in env."}

    run_id = uuid.uuid4().hex[:8]
    out_path = Path(out_dir) if out_dir else (Path.cwd() / "output" / run_id)
    out_path.mkdir(parents=True, exist_ok=True)
    media_dir = out_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    node = app.node_id
    app.note(f"reel-af: starting run {run_id} for {url}", tags=["reel", "start"])
    t_pipeline = time.time()

    # ───────────────────────────────────────────────────────────────
    # Dependency graph (* = parallel branches):
    #   extract_source                                  (serial — root)
    #     │
    #     ├── compose_script ──┐
    #     └── build_vocab    *─┤                        (vocab only needs summary)
    #                          ↓
    #                       break_scenes                 (needs script)
    #                          ↓
    #     ┌── plan_visual_arc ─┴── synthesize_audio *   (audio only needs script+scenes)
    #     ↓
    #     generate_shot_plans                            (needs arc + vocab)
    #     ↓
    #     generate_videos_phase   ←── (audio task still in flight if slow)
    #     ↓
    #     await audio_task                               (almost always already done)
    #     ↓
    #     assemble_final
    # ───────────────────────────────────────────────────────────────

    # 1. extract_source — serial root.
    t = time.time()
    extracted = await app.call(f"{node}.reel_extract_source", url=url)
    timings["extract_source"] = round(time.time() - t, 1)
    summary = extracted["summary"]

    # topic_familiarity drives obscure-vs-hot accessibility register;
    # content_mode drives general-vs-scientific. Both propagate through
    # vocabulary, arc, and shot director.
    topic_familiarity = summary.get("topic_familiarity", "hot")
    content_mode = summary.get("content_mode", "general")

    # 2. compose_script ∥ build_vocab_step — vocab only needs summary, so
    # fire it now to hide its 30-60s cost under compose_script's ~150s cost.
    t = time.time()
    compose_task = asyncio.create_task(
        app.call(f"{node}.reel_compose_script", summary=summary)
    )
    vocab_task = asyncio.create_task(
        app.call(f"{node}.reel_build_vocab_step", summary=summary)
    )
    composed = await compose_task
    timings["compose_script"] = round(time.time() - t, 1)
    draft = composed["draft"]

    # 3. break_scenes — needs script.
    t = time.time()
    scenes_resp = await app.call(
        f"{node}.reel_break_scenes_step", script=draft["script"]
    )
    scenes = scenes_resp["scenes"]
    timings["break_scenes"] = round(time.time() - t, 1)

    # Wait for vocab (almost always already done).
    vocab_resp = await vocab_task

    # 4. plan_visual_arc ∥ synthesize_audio — independent:
    #    arc needs vocab + scenes; audio needs script + scenes. Audio is the
    #    slow one (~30-60s TTS) — start it now so it's done by the time we
    #    finish shot_plans + videos.
    t = time.time()
    arc_task = asyncio.create_task(app.call(
        f"{node}.reel_plan_visual_arc_step",
        scenes=scenes, vocabulary=vocab_resp["vocabulary"],
        tone=draft["voice_tone"], script=draft["script"],
        topic_familiarity=topic_familiarity, content_mode=content_mode,
    ))
    audio_task = asyncio.create_task(app.call(
        f"{node}.reel_synthesize_audio",
        full_script=draft["script"], scenes=scenes,
        voice_tone=draft["voice_tone"], out_dir=str(media_dir),
    ))
    arc_resp = await arc_task
    timings["plan_visual_arc"] = round(time.time() - t, 1)

    # 5. generate_shot_plans — needs arc + vocab. Audio still streaming in
    # background.
    t = time.time()
    shot_plans_resp = await app.call(
        f"{node}.reel_generate_shot_plans",
        scenes=scenes, visual_arc=arc_resp["visual_arc"],
        vocabulary=vocab_resp["vocabulary"],
        tone=draft["voice_tone"], full_script=draft["script"],
        topic_familiarity=topic_familiarity, content_mode=content_mode,
    )
    timings["generate_shot_plans"] = round(time.time() - t, 1)
    plans = shot_plans_resp["plans"]

    # 6. Video generation (Veo i2v per scene, parallel internally).
    # Audio task continues running in the background.
    t = time.time()
    videos_resp = await app.call(
        f"{node}.reel_generate_videos_phase",
        scenes=scenes, plans=plans, out_dir=str(media_dir),
    )
    timings["videos"] = round(time.time() - t, 1)

    # Now collect audio (almost certainly already done — it had ~5 minutes
    # of cover while we ran arc + shots + videos).
    t = time.time()
    audio_resp = await audio_task
    timings["audio_wait"] = round(time.time() - t, 1)

    # 7. Assemble final reel.
    t = time.time()
    final = await app.call(
        f"{node}.reel_assemble_final",
        scenes=scenes, plans=plans,
        video_segments=videos_resp["videos"],
        audio_segments=audio_resp["segments"],
        direction=draft["direction"],
        hook_trick=draft["hook_trick"],
        retention_trick=draft["retention_trick"],
        close_trick=draft["close_trick"],
        viral_score=draft["viral_score"],
        out_dir=str(out_path), run_id=run_id,
    )
    timings["assemble"] = round(time.time() - t, 1)
    timings["total"] = round(time.time() - t_pipeline, 1)

    app.note(
        f"reel-af: run {run_id} done → {final['video_path']}",
        tags=["reel", "done"],
    )
    return {
        "video_path": final["video_path"],
        "duration_s": final["duration_s"],
        "script": draft["script"],
        "direction": draft["direction"],
        "voice_tone": draft["voice_tone"],
        "voice_id": audio_resp["voice"],
        "tricks": {
            "hook": draft["hook_trick"],
            "retention": draft["retention_trick"],
            "close": draft["close_trick"],
        },
        "self_score": composed["self_score"],
        "chosen_arch": composed["chosen_arch"],
        "captions": [s["caption"] for s in scenes],
        "motifs": [m["motif_id"] for m in vocab_resp["vocabulary"]["motifs"]],
        "content_mode": content_mode,
        "topic_familiarity": topic_familiarity,
        "audience_level": summary.get("audience_level", "general"),
        "run_id": run_id,
        "timings_s": timings,
    }


app.include_router(reel)


# Health endpoint so `af server` discovery + docker healthchecks work.
def _health() -> dict:
    return {"status": "ok", "service": "reel-af", "version": "0.1.0"}


from typing import cast as _cast  # noqa: E402

_cast(Any, app).add_api_route("/health", _health, methods=["GET"])


def main() -> None:
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8002")),
        auto_port=False,
    )


if __name__ == "__main__":
    main()
