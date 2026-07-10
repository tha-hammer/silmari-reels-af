"""reel-af — turn a URL or a topic into a vertical viral reel.

Two entry reasoners; everything else is a supporting reasoner in the
control-plane DAG so the workflow is fully introspectable.

    article_to_reel(url)   ─┐
                            │   shared downstream:
    topic_to_reel(topic)   ─┤   synthesize_audio → pack_cards → plan_beats
                                → plan_visuals ∥ plan_accents
                                → generate_videos → stitch_reel
                                → final mp4

Article path:
    URL → extract_essence → compose_script → (audio, beats, visuals,
    accents, videos) → stitch_reel

Topic path (multi-reasoner hunter cascade):
    topic → 4 parallel hunters (specific_figure, reversal, temporal,
            cross_domain) producing 12 candidates → critic picks top 3
            → 3 parallel narrators write delayed-reveal scripts →
            pairwise judge picks the winner → the same downstream
            audio/beats/visuals/accents/videos/stitch path.

Run the agent (registers with the AgentField control plane, serves
reasoners on :8002):

    af server                                  # control plane
    cd examples/reel-af && uv run python -m reel_af.app

Invoke async, get an execution_id immediately:

    curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_article_to_reel \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"url":"https://example.com/article"}}'

    curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_topic_to_reel \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"topic":"philosophy of mind"}}'
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

from agentfield import Agent, AgentRouter, AIConfig  # noqa: E402

# Apply SDK bug-fixes at startup so every OpenRouterProvider call gets
# the fixed behaviour. Module is idempotent.
import reel_af.sdk_patches  # noqa: E402, F401

app = Agent(
    node_id=os.getenv("AGENT_NODE_ID", "reel-af"),
    agentfield_server=os.getenv("AGENTFIELD_SERVER", "http://localhost:8080"),
    # Control-plane API key (distinct from the LLM key in ai_config below). The
    # SDK only sends the X-API-Key registration header when this is set; without
    # it, an auth-enabled control plane rejects registration with HTTP 401.
    api_key=os.getenv("AGENTFIELD_API_KEY"),
    version="1.0.0",
    description="URL or topic → vertical viral reel via a multi-reasoner DAG.",
    ai_config=AIConfig(
        model=os.getenv("REEL_AF_MODEL", "openrouter/deepseek/deepseek-v4-pro"),
        # Reasoning endpoint defaults to OpenRouter. Advanced users can point
        # `.ai()` calls at any OpenAI-compatible endpoint (local vLLM/Ollama,
        # a self-hosted gateway, another aggregator) without code changes:
        #   REEL_AF_API_KEY  overrides the key (falls back to OPENROUTER_API_KEY)
        #   REEL_AF_API_BASE overrides the base URL (empty/unset → OpenRouter)
        # Media (TTS/image/video) still routes through OpenRouter, so
        # OPENROUTER_API_KEY remains required. See README "Bring your own model".
        api_key=os.getenv("REEL_AF_API_KEY") or os.environ.get("OPENROUTER_API_KEY", ""),
        api_base=os.getenv("REEL_AF_API_BASE") or "https://openrouter.ai/api/v1",
    ),
    dev_mode=True,
)

# All reel reasoners live under prefix "reel".
reel = AgentRouter(prefix="reel", tags=["video", "viral"])


# ════════════════════════════════════════════════════════════════════
# Article-only reasoners — URL → Essence → ScriptDraft
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def extract_essence(url: str) -> dict:
    """Phase 1 — URL → Essence (one harness call).

    Fetches the article, distills the most surprising claim + mechanism
    + evidence + content_mode + domain in one structured pass.
    """
    from reel_af.agents.extract import extract_essence as _extract

    essence = await _extract(app, url)
    return {"essence": essence.model_dump()}


@reel.reasoner()
async def compose_script(essence: dict) -> dict:
    """Phase 2 — Essence → ScriptDraft (one .ai() call).

    Fixed Hook → Mechanism → Payoff structure with inline Gemini TTS
    audio tags. The schema's loop-back validator enforces the final
    clause to echo a keyword from the hook so the reel rewatches.
    """
    from reel_af.agents.compose import compose_script as _compose
    from reel_af.models import Essence

    e = Essence(**essence)
    script = await _compose(app, e)
    return {"script": script.model_dump()}


# ════════════════════════════════════════════════════════════════════
# Topic-only reasoners — 4 hunters + critic + narrator + judge
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def hunt_specific_figure(topic: str) -> dict:
    """Hunter A — 3 candidates centered on a NAMED person most viewers
    haven't heard of. Names the year, names the finding."""
    from reel_af.agents.hunters import hunt_specific_figure as _hunt

    candidates = await _hunt(app, topic)
    return {"candidates": [c.model_dump() for c in candidates]}


@reel.reasoner()
async def hunt_reversal(topic: str) -> dict:
    """Hunter B — 3 candidates where the common interpretation is
    BACKWARDS, with the source of the reversal named."""
    from reel_af.agents.hunters import hunt_reversal as _hunt

    candidates = await _hunt(app, topic)
    return {"candidates": [c.model_dump() for c in candidates]}


@reel.reasoner()
async def hunt_temporal(topic: str) -> dict:
    """Hunter C — 3 candidates tied to a specific year/event that
    reframes how we see the field."""
    from reel_af.agents.hunters import hunt_temporal as _hunt

    candidates = await _hunt(app, topic)
    return {"candidates": [c.model_dump() for c in candidates]}


@reel.reasoner()
async def hunt_cross_domain(topic: str) -> dict:
    """Hunter D — 3 candidates bridging the topic to an unexpected
    other field, with the specific bridge-builder named."""
    from reel_af.agents.hunters import hunt_cross_domain as _hunt

    candidates = await _hunt(app, topic)
    return {"candidates": [c.model_dump() for c in candidates]}


@reel.reasoner()
async def pick_top_essences(
    topic: str, candidates: list[dict], n_top: int = 3,
) -> dict:
    """Critic — scores 12 candidates on novelty / specificity /
    hookability / narratability and picks the top N preferring angle
    diversity when scores are close."""
    from reel_af.agents.critic import pick_top_essences as _crit
    from reel_af.models import EssenceCandidate

    cand_objs = [EssenceCandidate(**c) for c in candidates]
    out = await _crit(app, topic, cand_objs, n=n_top)
    chosen = [i for i in out.chosen_indices if 0 <= i < len(cand_objs)]
    return {
        "rankings": [r.model_dump() for r in out.rankings],
        "chosen_indices": chosen,
        "chosen_essences": [cand_objs[i].model_dump() for i in chosen],
    }


@reel.reasoner()
async def write_narrations(essences: list[dict]) -> dict:
    """Narrator fan-out — one delayed-reveal script per chosen essence
    (parallel via asyncio.gather)."""
    from reel_af.agents.narrator import write_narrations as _narr
    from reel_af.models import EssenceCandidate

    ess_objs = [EssenceCandidate(**e) for e in essences]
    scripts = await _narr(app, ess_objs)
    return {"scripts": [s.model_dump() for s in scripts]}


@reel.reasoner()
async def pick_best_narration(
    topic: str, scripts: list[dict], essences: list[dict],
) -> dict:
    """Pairwise judge — picks the most viral script."""
    from reel_af.agents.judge import pick_best_narration as _judge
    from reel_af.models import ConversationalScript, EssenceCandidate

    script_objs = [ConversationalScript(**s) for s in scripts]
    ess_objs = [EssenceCandidate(**e) for e in essences]
    if len(script_objs) == 1:
        return {"winner_idx": 0, "composite_score": 7.0, "why": "only one"}
    verdict = await _judge(app, topic, script_objs, ess_objs)
    return verdict.model_dump()


# ════════════════════════════════════════════════════════════════════
# Shared downstream — audio, beats, visuals, accents, videos, stitch
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def synthesize_audio(
    narration: str, voice_tone: str, out_dir: str,
) -> dict:
    """Sentence-by-sentence TTS + sample-accurate word timings.

    Each sentence synthesizes independently (in parallel), is
    sped-up via ffmpeg ``atempo`` (preserves pitch), measured with
    ffprobe, then native-wave concatenated. Sentence boundaries are
    sample-accurate on the final timeline; words within a sentence
    are distributed by syllable count.
    """
    from reel_af.render.tts import (
        strip_tts_tags,
        synthesize_audio as _synth,
        voice_for_tone,
    )

    voice = voice_for_tone(voice_tone)
    audio_path, word_timings = await _synth(
        narration=narration, voice=voice, out_dir=Path(out_dir),
    )
    duration_s = word_timings[-1].end_s if word_timings else 0.0
    return {
        "audio_path": str(audio_path),
        "duration_s": duration_s,
        "voice": voice,
        "word_timings": [w.model_dump() for w in word_timings],
        "spoken_narration": strip_tts_tags(narration),
    }


@reel.reasoner()
async def pack_cards(word_timings: list[dict]) -> dict:
    """WordTimings → Cards for subtitle layout. Pure code, no LLM."""
    from reel_af.models import WordTiming
    from reel_af.planning.cards import pack_cards as _pack

    wts = [WordTiming(**w) for w in word_timings]
    cards = _pack(wts)
    return {"cards": [c.model_dump() for c in cards]}


@reel.reasoner()
async def plan_beats(script: dict, audio_duration_s: float) -> dict:
    """ScriptDraft → Beats. One Beat per (hook | mechanism_line | payoff).

    Each beat gets a fixed Veo bucket (4 / 6 / 8 s) chosen by role
    (hook floors at 6s; payoff caps at 4s for the snappy loop close;
    mechanism uses the smallest bucket ≥ word-share estimate + safety).
    """
    from reel_af.models import ScriptDraft
    from reel_af.planning.beats import plan_beats as _plan

    s = ScriptDraft(**script)
    beats = _plan(s, audio_duration_s)
    if not beats:
        raise RuntimeError("plan_beats: zero beats from non-empty script.")
    return {"beats": [b.model_dump() for b in beats]}


@reel.reasoner()
async def plan_visuals(
    beats: list[dict], essence: dict, spoken_narration: str,
) -> dict:
    """Per-beat first-frame image prompt + motion hint (parallel fan-out)."""
    from reel_af.agents.visual import plan_beat_visuals
    from reel_af.models import Beat, Essence

    beat_objs = [Beat(**b) for b in beats]
    e = Essence(**essence)
    visuals = await plan_beat_visuals(
        app, beat_objs, e, full_narration=spoken_narration,
    )
    return {"visuals": [v.model_dump() for v in visuals]}


@reel.reasoner()
async def plan_accents(beats: list[dict], essence: dict) -> dict:
    """Per-beat optional editorial overlay (parallel fan-out, biased to None).

    Six canonical patterns: number, named_entity, jargon_translation,
    hook_title_card, reaction, list_marker. Most beats return None.
    """
    from reel_af.agents.accent import plan_beat_accents
    from reel_af.models import Beat, Essence

    beat_objs = [Beat(**b) for b in beats]
    e = Essence(**essence)
    accents = await plan_beat_accents(app, beat_objs, e)
    return {
        "accents": [a.model_dump() if a is not None else None for a in accents],
    }


@reel.reasoner()
async def generate_videos(
    beats: list[dict],
    visuals: list[dict],
    content_mode: str,
    out_dir: str,
) -> dict:
    """Per-beat first-frame + Veo i2v (parallel fan-out).

    Two-tier fallback per beat: image fail → placeholder + ken-burns;
    Veo fail → real first-frame + ken-burns. A single beat failure
    never crashes the whole reel.
    """
    from reel_af.models import Beat, BeatVisual
    from reel_af.render.video import generate_beat_videos

    beat_objs = [Beat(**b) for b in beats]
    visual_objs = [BeatVisual(**v) for v in visuals]
    media_dir = Path(out_dir) / "media"
    artifacts = await generate_beat_videos(
        beats=beat_objs,
        visuals=visual_objs,
        out_dir=media_dir,
        content_mode=content_mode,
    )
    return {"artifacts": [a.model_dump(mode="json") for a in artifacts]}


@reel.reasoner()
async def stitch_reel(
    beats: list[dict],
    artifacts: list[dict],
    cards: list[dict],
    accents: list[dict | None],
    audio_path: str,
    out_dir: str,
    run_id: str,
) -> dict:
    """Single-pass ffmpeg: concat filter + libass + AAC mux."""
    from reel_af.models import AccentOverlay, Beat, BeatArtifact, Card
    from reel_af.render.stitch import stitch_reel as _stitch

    beat_objs = [Beat(**b) for b in beats]
    artifact_objs = [BeatArtifact(**a) for a in artifacts]
    card_objs = [Card(**c) for c in cards]
    accent_objs = [
        AccentOverlay(**a) if a is not None else None for a in accents
    ]
    final = await _stitch(
        beats=beat_objs,
        artifacts=artifact_objs,
        cards=card_objs,
        accents=accent_objs,
        full_audio_path=Path(audio_path),
        out_dir=Path(out_dir),
        run_id=run_id,
    )
    return {"video_path": str(final)}


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER 1 — Article → Reel
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def article_to_reel(
    url: str, out_dir: str | None = None,
) -> dict:
    """Turn an article URL into a vertical viral reel.

    Composes the DAG via ``app.call`` so every phase is a visible node
    in the control plane and individually re-runnable.

    Example:
      curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_article_to_reel \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"url":"https://example.com/article"}}'
    """
    if "OPENROUTER_API_KEY" not in os.environ:
        return {"error": "OPENROUTER_API_KEY not set in env."}

    run_id = uuid.uuid4().hex[:8]
    out_path = (
        Path(out_dir) if out_dir else (Path.cwd() / "output" / f"article-{run_id}")
    )
    out_path.mkdir(parents=True, exist_ok=True)
    media_dir = out_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    node = app.node_id
    app.note(
        f"reel-af article: starting run {run_id} for {url}",
        tags=["reel", "article", "start"],
    )
    t_pipeline = time.time()

    # Phase 1 — extract
    t = time.time()
    e_out = await app.call(f"{node}.reel_extract_essence", url=url)
    timings["extract"] = round(time.time() - t, 1)
    essence = e_out["essence"]

    # Phase 2 — compose
    t = time.time()
    c_out = await app.call(f"{node}.reel_compose_script", essence=essence)
    timings["compose"] = round(time.time() - t, 1)
    script = c_out["script"]

    final = await _render_downstream(
        node=node,
        essence=essence,
        script=script,
        out_path=out_path,
        media_dir=media_dir,
        run_id=run_id,
        timings=timings,
    )

    timings["total"] = round(time.time() - t_pipeline, 1)
    app.note(
        f"reel-af article: run {run_id} done → {final['video_path']}",
        tags=["reel", "article", "done"],
    )
    return {
        **final,
        "source": "article",
        "url": url,
        "hook": script["hook"],
        "hook_variant": script["hook_variant"],
        "content_mode": essence["content_mode"],
        "domain": essence["domain"],
        "run_id": run_id,
        "timings_s": timings,
    }


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER 2 — Topic → Reel
# ════════════════════════════════════════════════════════════════════


@reel.reasoner()
async def topic_to_reel(
    topic: str, out_dir: str | None = None,
) -> dict:
    """Turn a topic string into a vertical viral reel.

    Internally runs an 8-node hunter cascade (4 parallel hunters →
    critic → 3 parallel narrators → pairwise judge) to produce a
    delayed-reveal narration, then the same shared downstream path as
    article_to_reel.

    Example:
      curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_topic_to_reel \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"topic":"philosophy of mind"}}'
    """
    if "OPENROUTER_API_KEY" not in os.environ:
        return {"error": "OPENROUTER_API_KEY not set in env."}

    run_id = uuid.uuid4().hex[:8]
    out_path = (
        Path(out_dir) if out_dir else (Path.cwd() / "output" / f"topic-{run_id}")
    )
    out_path.mkdir(parents=True, exist_ok=True)
    media_dir = out_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    node = app.node_id
    app.note(
        f"reel-af topic: starting run {run_id} for topic={topic!r}",
        tags=["reel", "topic", "start"],
    )
    t_pipeline = time.time()

    # Phase 1 — 4 parallel hunters
    t = time.time()
    h_results = await asyncio.gather(
        app.call(f"{node}.reel_hunt_specific_figure", topic=topic),
        app.call(f"{node}.reel_hunt_reversal", topic=topic),
        app.call(f"{node}.reel_hunt_temporal", topic=topic),
        app.call(f"{node}.reel_hunt_cross_domain", topic=topic),
    )
    all_candidates: list[dict] = [
        c for batch in h_results for c in batch["candidates"]
    ]
    timings["hunt"] = round(time.time() - t, 1)

    # Phase 2 — critic picks top 3
    t = time.time()
    crit_out = await app.call(
        f"{node}.reel_pick_top_essences",
        topic=topic,
        candidates=all_candidates,
        n_top=3,
    )
    timings["critic"] = round(time.time() - t, 1)
    chosen_essences: list[dict] = crit_out["chosen_essences"]

    # Phase 3 — 3 narrators in parallel
    t = time.time()
    narr_out = await app.call(
        f"{node}.reel_write_narrations",
        essences=chosen_essences,
    )
    timings["narrate"] = round(time.time() - t, 1)
    scripts: list[dict] = narr_out["scripts"]

    # Phase 4 — judge picks winner
    t = time.time()
    verdict = await app.call(
        f"{node}.reel_pick_best_narration",
        topic=topic,
        scripts=scripts,
        essences=chosen_essences,
    )
    timings["judge"] = round(time.time() - t, 1)
    winner_idx = max(0, min(verdict["winner_idx"], len(scripts) - 1))
    winner_script = scripts[winner_idx]
    winner_essence = chosen_essences[winner_idx]

    # ── Map ConversationalScript → ScriptDraft for the downstream ─────
    # Beats/visuals/accents all operate on ScriptDraft; the topic
    # pipeline writes ConversationalScripts so we adapt the field
    # shapes (tease → hook, reveal-sentences → mechanism_lines, …).
    reveal_sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", winner_script["reveal"].strip())
        if s.strip()
    ]
    if len(reveal_sentences) < 2:
        # ScriptDraft requires min_length=2 on mechanism_lines.
        reveal_sentences = (
            reveal_sentences + [winner_script.get("common_belief") or "."]
        )[:2]

    essence = {
        "core_claim": winner_essence["core_claim"],
        "mechanism": winner_essence["mechanism"],
        "evidence": winner_essence["evidence"],
        "content_mode": "general",  # topic-derived → conversational register
        "domain": winner_essence["domain"],
    }
    script = {
        "hook": winner_script["tease"],
        "hook_variant": "curiosity_gap",
        "mechanism_lines": reveal_sentences[:4],
        "payoff_line": winner_script["payoff"],
        "target_wpm": 180,
        "narration": winner_script["narration"],
    }

    final = await _render_downstream(
        node=node,
        essence=essence,
        script=script,
        out_path=out_path,
        media_dir=media_dir,
        run_id=run_id,
        timings=timings,
    )

    timings["total"] = round(time.time() - t_pipeline, 1)
    app.note(
        f"reel-af topic: run {run_id} done → {final['video_path']}",
        tags=["reel", "topic", "done"],
    )
    return {
        **final,
        "source": "topic",
        "topic": topic,
        "tease": winner_script["tease"],
        "reveal": winner_script["reveal"],
        "payoff": winner_script["payoff"],
        "common_belief": winner_script.get("common_belief"),
        "open_style": winner_script["open_style"],
        "chosen_essence": winner_essence,
        "winner_composite": verdict.get("composite_score"),
        "winner_why": verdict.get("why"),
        "all_candidates": all_candidates,
        "all_narrations": [s["narration"] for s in scripts],
        "run_id": run_id,
        "timings_s": timings,
    }


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER 3 — Video (URL) → Reel  (composite overlay pipeline)
# ════════════════════════════════════════════════════════════════════


def _run_composite_reels(
    *, url: str, preset_name: str, count: int, out_path: Path, chrome: str | None,
) -> dict:
    """Blocking: download the source video, transcribe it, and cut ``count``
    preset-length reels with a Remotion overlay. Reuses the exact building
    blocks behind the ``reel-af reels`` CLI (no LLM / media API calls)."""
    import shutil
    import subprocess

    from reel_af.render import middle_third
    from reel_af.render.captions import caption_words
    from reel_af.render.hooks import download_crisp_source
    from reel_af.render.presets import load_preset, preset_names

    try:
        cfg = load_preset(preset_name)
    except KeyError:
        return {"error": f"unknown preset {preset_name!r}; available: {preset_names()}"}
    if cfg.get("overlay") != "middle_third":
        return {"error": (f"preset {preset_name!r} (overlay={cfg.get('overlay')!r}) is not "
                          "yet wired for video intake — only 'middle_third' is.")}

    fps = int(cfg.get("fps", 30))
    reel_s = float(cfg["reel_seconds"])
    try:
        src = download_crisp_source(url, out_path / "source.mp4")
    except (RuntimeError, ValueError) as exc:
        return {"error": str(exc)}
    words = caption_words(src, workdir=out_path)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True).stdout.strip())
    n = int(dur // reel_s)
    if n < 1:
        return {"error": f"source is {dur:.0f}s — shorter than one {reel_s:.0f}s reel."}

    reels: list[str] = []
    for idx in range(1, min(max(1, count), n) + 1):
        t0 = (idx - 1) * reel_s
        d = out_path / f"reel{idx:02d}"
        seq = d / "seq"
        segs = middle_third.window_segments(words, t0, t0 + reel_s, cfg, fps=fps)
        overlay = middle_third.render_overlay(segs, int(reel_s * fps), seq, cfg, chrome=chrome)
        final = middle_third.composite_window(src, t0, reel_s, overlay, d / f"reel{idx:02d}.mp4", fps=fps)
        shutil.rmtree(seq, ignore_errors=True)
        reels.append(str(final))
    return {"video_path": reels[0], "reels": reels, "reel_count": len(reels),
            "source_seconds": round(dur, 1)}


@reel.reasoner()
async def composite_to_reel(
    url: str,
    preset: str = "middle-third-dynamic",
    count: int = 1,
    out_dir: str | None = None,
) -> dict:
    """Turn a source VIDEO (URL) into vertical reel(s) with a Remotion overlay.

    Downloads the video, transcribes it, and cuts ``count`` preset-length reels
    (default 1) each with a script-synced middle-third overlay — the same
    pipeline as the ``reel-af reels`` CLI. Purely mechanical (ffmpeg + whisper +
    Node/Remotion + Chromium); no LLM or media API keys required.

    Example:
      curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_composite_to_reel \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"url":"https://youtu.be/…","preset":"middle-third-dynamic"}}'
    """
    run_id = uuid.uuid4().hex[:8]
    out_path = Path(out_dir) if out_dir else (Path.cwd() / "output" / f"composite-{run_id}")
    out_path.mkdir(parents=True, exist_ok=True)
    chrome = os.getenv("CHROMIUM_PATH") or None

    app.note(f"reel-af composite: run {run_id} url={url!r} preset={preset!r}",
             tags=["reel", "composite", "start"])
    t_start = time.time()
    result = await asyncio.to_thread(
        _run_composite_reels, url=url, preset_name=preset,
        count=max(1, int(count)), out_path=out_path, chrome=chrome)
    took = round(time.time() - t_start, 1)
    meta = {"source": "video", "url": url, "preset": preset, "run_id": run_id}
    if "error" in result:
        app.note(f"reel-af composite: run {run_id} failed — {result['error']}",
                 tags=["reel", "composite", "error"])
        return {**result, **meta}
    app.note(f"reel-af composite: run {run_id} done → {result['video_path']} ({took}s)",
             tags=["reel", "composite", "done"])
    return {**result, **meta, "timings_s": {"total": took}}


# ════════════════════════════════════════════════════════════════════
# Shared downstream orchestrator
# ════════════════════════════════════════════════════════════════════


async def _render_downstream(
    *,
    node: str,
    essence: dict,
    script: dict,
    out_path: Path,
    media_dir: Path,
    run_id: str,
    timings: dict[str, float],
) -> dict:
    """audio → cards/beats (parallel) → visuals/accents (parallel) →
    videos → stitch. Used by both entry points."""

    # TTS — sentence-by-sentence
    t = time.time()
    a_out = await app.call(
        f"{node}.reel_synthesize_audio",
        narration=script["narration"],
        voice_tone="wonder",
        out_dir=str(media_dir),
    )
    timings["tts"] = round(time.time() - t, 1)

    # Cards (subtitle layout) ∥ Beats (video planning)
    t = time.time()
    cards_task = asyncio.create_task(app.call(
        f"{node}.reel_pack_cards",
        word_timings=a_out["word_timings"],
    ))
    beats_task = asyncio.create_task(app.call(
        f"{node}.reel_plan_beats",
        script=script,
        audio_duration_s=a_out["duration_s"],
    ))
    cards_out, beats_out = await asyncio.gather(cards_task, beats_task)
    timings["plan"] = round(time.time() - t, 1)
    cards = cards_out["cards"]
    beats = beats_out["beats"]

    # Visuals ∥ Accents
    t = time.time()
    visuals_task = asyncio.create_task(app.call(
        f"{node}.reel_plan_visuals",
        beats=beats,
        essence=essence,
        spoken_narration=a_out["spoken_narration"],
    ))
    accents_task = asyncio.create_task(app.call(
        f"{node}.reel_plan_accents",
        beats=beats,
        essence=essence,
    ))
    v_out, ac_out = await asyncio.gather(visuals_task, accents_task)
    timings["visual_accent"] = round(time.time() - t, 1)

    # Videos
    t = time.time()
    g_out = await app.call(
        f"{node}.reel_generate_videos",
        beats=beats,
        visuals=v_out["visuals"],
        content_mode=essence["content_mode"],
        out_dir=str(out_path),
    )
    timings["media"] = round(time.time() - t, 1)

    # Stitch
    t = time.time()
    s_out = await app.call(
        f"{node}.reel_stitch_reel",
        beats=beats,
        artifacts=g_out["artifacts"],
        cards=cards,
        accents=ac_out["accents"],
        audio_path=a_out["audio_path"],
        out_dir=str(out_path),
        run_id=run_id,
    )
    timings["stitch"] = round(time.time() - t, 1)

    return {
        "video_path": s_out["video_path"],
        "duration_s": a_out["duration_s"],
        "narration": a_out["spoken_narration"],
        "voice_id": a_out["voice"],
        "beat_count": len(beats),
        "card_count": len(cards),
        "accent_count": sum(1 for a in ac_out["accents"] if a is not None),
    }


# ════════════════════════════════════════════════════════════════════
# Server
# ════════════════════════════════════════════════════════════════════


app.include_router(reel)


def _health() -> dict:
    return {"status": "ok", "service": "reel-af", "version": "1.0.0"}


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
