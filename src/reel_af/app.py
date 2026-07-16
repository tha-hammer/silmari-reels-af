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
import inspect
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

from agentfield import Agent, AgentRouter, AIConfig  # noqa: E402
from agentfield.media_providers import OpenRouterProvider  # noqa: E402

# Apply SDK bug-fixes at startup so every OpenRouterProvider call gets
# the fixed behaviour. Module is idempotent.
import reel_af.sdk_patches  # noqa: E402, F401
from reel_af.agents.extract import essence_from_text  # noqa: E402
from reel_af.dsl.compile import compile_composite, load_words  # noqa: E402
from reel_af.dsl.composite import read_composite_file  # noqa: E402
from reel_af.dsl.cutins import map_cut_ins  # noqa: E402
from reel_af.dsl.models import (  # noqa: E402
    A1_DELIVERY_UNAVAILABLE,
    BROWSER_DELIVERABLE_SCHEMES,
    DSL_HOOKS_WORKFLOW,
    CompileContext,
    CutInSpec,
    RenderabilityError,
    SourceRef,
    validate_renderable,
)
from reel_af.models import Essence  # noqa: E402
from reel_af.naming import reel_output_name  # noqa: E402
from reel_af.render.finish import FinishContext, finish_reel  # noqa: E402
from reel_af.render.finish_config import ReelFinishConfig  # noqa: E402
from reel_af.render.footage_stitch import download_segments, stitch_footage_reel  # noqa: E402
from reel_af.render.images import generate_first_frame  # noqa: E402
from reel_af.render.presets import load_preset  # noqa: E402

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

_CAROUSEL_CONFIG_PATH = Path(__file__).parent / "render" / "config" / "carousel.json"


@lru_cache(maxsize=1)
def _carousel_config() -> dict[str, Any]:
    return json.loads(_CAROUSEL_CONFIG_PATH.read_text())


_CAROUSEL_CFG = _carousel_config()
CAROUSEL_DEFAULT_PRESET = str(_CAROUSEL_CFG["default_preset"])
CAROUSEL_DEFAULT_CROP = str(_CAROUSEL_CFG["default_crop"])
_CAROUSEL_MIN_SLIDE_COUNT = int(_CAROUSEL_CFG["min_slide_count"])
_CAROUSEL_RUN_ID_HEX_CHARS = int(_CAROUSEL_CFG["run_id_hex_chars"])
_CAROUSEL_OUTPUT_ROOT = str(_CAROUSEL_CFG["output_root"])
_CAROUSEL_OUTPUT_DIR_PREFIX = str(_CAROUSEL_CFG["output_dir_prefix"])
_CAROUSEL_OPENROUTER_ERROR = str(_CAROUSEL_CFG["missing_openrouter_error"])
_CAROUSEL_PROMPT_COUNT_ERROR_TEMPLATE = str(
    _CAROUSEL_CFG["planner_wrong_count_error_template"]
)
_CAROUSEL_NEGATIVE_IDX_ERROR_TEMPLATE = str(
    _CAROUSEL_CFG["negative_idx_error_template"]
)
_CAROUSEL_PROMPT_USER_TEMPLATE = str(_CAROUSEL_CFG["prompt_user_template"])
_CAROUSEL_PROMPT_SYSTEM = str(_CAROUSEL_CFG["prompt_system"])
_SLIDE_STATUS_OK = "ok"
_SLIDE_STATUS_FAILED = "failed"


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
        voice_for_tone,
    )
    from reel_af.render.tts import (
        synthesize_audio as _synth,
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
    # T10: deliver the reel out via the shared bucket so the browser can download it,
    # under a descriptive, collision-safe basename derived from the article's core claim.
    from reel_af.storage import upload_reel

    filename = reel_output_name(
        essence.get("core_claim") or essence.get("domain"), run_id, datetime.now(timezone.utc).date()
    )
    download_url = await asyncio.to_thread(
        upload_reel, final["video_path"], run_id=run_id, filename=filename
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
        **({"download_url": download_url} if download_url else {}),
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
    # T10: deliver the reel out via the shared bucket so the browser can download it,
    # under a descriptive, collision-safe basename derived from the topic.
    from reel_af.storage import upload_reel

    filename = reel_output_name(topic, run_id, datetime.now(timezone.utc).date())
    download_url = await asyncio.to_thread(
        upload_reel, final["video_path"], run_id=run_id, filename=filename
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
        **({"download_url": download_url} if download_url else {}),
        "timings_s": timings,
    }


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER 3 — Video (URL) → Reel  (composite overlay pipeline)
# ════════════════════════════════════════════════════════════════════

SOURCE_NO_AUDIO_TRACK_CODE = "source_no_audio_track"
SOURCE_NO_AUDIO_TRACK_MESSAGE = (
    "source video has no audio track; the composite preset transcribes spoken audio "
    "to build the overlay. Upload a clip that has an audio track."
)


@dataclass
class CompositeDeps:
    """The external I/O the composite pipeline drives (download, audio probe,
    transcribe, duration probe). Injected so the merge + prop-emission span is
    drivable in tests without ffmpeg/whisper/network; production builds the real
    wiring via :func:`_default_composite_deps`."""

    download: Any
    has_audio: Any
    transcribe: Any
    probe_duration: Any


def _ffprobe_duration(src: Path) -> float:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _default_composite_deps() -> CompositeDeps:
    from reel_af.render.captions import caption_words, has_audio_stream
    from reel_af.render.hooks import download_crisp_source

    return CompositeDeps(
        download=download_crisp_source,
        has_audio=has_audio_stream,
        transcribe=caption_words,
        probe_duration=_ffprobe_duration,
    )


def _run_composite_reels(
    *, url: str, preset_name: str, count: int, out_path: Path, chrome: str | None,
    overrides: dict | None = None,
    deps: CompositeDeps | None = None,
    runner: Any = None,
) -> dict:
    """Blocking: download the source video, transcribe it, and cut ``count``
    preset-length reels with a Remotion overlay. Reuses the exact building
    blocks behind the ``reel-af reels`` CLI (no LLM / media API calls).

    ``overrides`` is a per-job tunable dict merged onto the loaded preset after
    ``safe_overrides`` drops unknown keys and clamps values (plan Behavior 1/2).
    ``deps``/``runner`` are injectable seams for the external I/O + render
    subprocess so the merge and prop emission are drivable without ffmpeg/whisper/
    Node (default to the real wiring)."""
    import shutil

    from reel_af.render import lower_third, middle_third
    from reel_af.render.presets import load_preset, preset_names
    from reel_af.render.tunables import safe_overrides

    if deps is None:
        deps = _default_composite_deps()
    if runner is None:
        import subprocess

        runner = subprocess.run

    try:
        cfg = {**load_preset(preset_name), **safe_overrides(overrides)}
    except KeyError:
        return {"error": f"unknown preset {preset_name!r}; available: {preset_names()}"}
    overlay_kind = cfg.get("overlay")
    if overlay_kind not in {"middle_third", "lower_third"}:
        return {"error": (f"preset {preset_name!r} (overlay={overlay_kind!r}) is not "
                          "wired for video intake.")}

    fps = int(cfg.get("fps", 30))
    reel_s = float(cfg["reel_seconds"])
    try:
        src = deps.download(url, out_path / "source.mp4")
    except (RuntimeError, ValueError) as exc:
        return {"error": str(exc)}
    source_has_audio = deps.has_audio(src)
    if not source_has_audio:
        return {"error": SOURCE_NO_AUDIO_TRACK_MESSAGE, "code": SOURCE_NO_AUDIO_TRACK_CODE}
    words = deps.transcribe(src, workdir=out_path)
    dur = float(deps.probe_duration(src))
    n = int(dur // reel_s)
    if n < 1:
        return {"error": f"source is {dur:.0f}s — shorter than one {reel_s:.0f}s reel."}

    reels: list[str] = []
    for idx in range(1, min(max(1, count), n) + 1):
        t0 = (idx - 1) * reel_s
        d = out_path / f"reel{idx:02d}"
        seq = d / "seq"
        if overlay_kind == "middle_third":
            segs = middle_third.window_segments(words, t0, t0 + reel_s, cfg, fps=fps)
            overlay = middle_third.render_overlay(
                segs, int(reel_s * fps), seq, cfg, chrome=chrome, runner=runner
            )
            final = middle_third.composite_window(
                src, t0, reel_s, overlay, d / f"reel{idx:02d}.mp4", fps=fps, runner=runner
            )
        else:
            title = lower_third.title_from_words(words, t0, t0 + reel_s, cfg)
            overlay = lower_third.render_lower_third(
                title,
                seq,
                accent=str(cfg.get("overlay_accent", "#7E22CE")),
                chrome=chrome,
                cfg=cfg,
                runner=runner,
            )
            final = lower_third.composite_window(
                src, t0, reel_s, overlay, d / f"reel{idx:02d}.mp4", fps=fps, cfg=cfg,
                runner=runner,
            )
        shutil.rmtree(seq, ignore_errors=True)
        reels.append(str(final))
    return {"video_path": reels[0], "reels": reels, "reel_count": len(reels),
            "source_seconds": round(dur, 1)}


@reel.reasoner()
async def composite_to_reel(
    url: str,
    preset: str = "middle-third-dynamic",
    count: int = 1,
    overrides: dict | None = None,
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
        count=max(1, int(count)), out_path=out_path, chrome=chrome,
        overrides=overrides)
    took = round(time.time() - t_start, 1)
    # NB: source ``url`` is intentionally NOT surfaced in the reel result — it is the
    # (presigned) input, and the UI must never present it as the reel download (T10).
    meta = {"source": "video", "preset": preset, "run_id": run_id}
    if "error" in result:
        app.note(f"reel-af composite: run {run_id} failed — {result['error']}",
                 tags=["reel", "composite", "error"])
        return {**result, **meta}
    # T10: the reel is on the node's ephemeral fs — deliver it out via the shared
    # bucket and hand the browser a presigned GET url. Fail-soft: None if unconfigured.
    from reel_af.storage import upload_reel

    # Decision C: use the public `preset` as the descriptive source (the source url is
    # intentionally not surfaced here for privacy).
    filename = reel_output_name(preset, run_id, datetime.now(timezone.utc).date())
    download_url = await asyncio.to_thread(
        upload_reel, result["video_path"], run_id=run_id, filename=filename
    )
    delivered = {"download_url": download_url} if download_url else {}
    app.note(f"reel-af composite: run {run_id} done → {result['video_path']} "
             f"(delivered={bool(download_url)}, {took}s)",
             tags=["reel", "composite", "done"])
    return {**result, **meta, **delivered, "timings_s": {"total": took}}


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER 4 — Research/Text → Carousel
# ════════════════════════════════════════════════════════════════════


class _FailClosedStoragePort:
    async def put(self, *, run_id, idx, path):
        raise RuntimeError("carousel StoragePort is not configured")


def _default_storage_port():
    return _FailClosedStoragePort()


async def _maybe_await(value):
    value_is_awaitable = inspect.isawaitable(value)
    if value_is_awaitable:
        return await value
    return value


async def _resolve_prompts(prompt_planner, essence, count: int) -> list[str]:
    prompts = await _maybe_await(prompt_planner(essence, count))
    return list(prompts or [])


def _slide_record(
    idx: int,
    image_prompt: str,
    image_ref: str | None,
    status: str,
    *,
    error: str | None = None,
) -> dict:
    record = {
        "idx": idx,
        "image_prompt": image_prompt,
        "image_ref": image_ref,
        "status": status,
    }
    if error is not None:
        record["error"] = error
    return record


async def plan_carousel_prompts(planner_app: Any, essence: Essence, count: int) -> list[str]:
    """Turn an Essence into ordered still-image prompts for a carousel."""
    n = max(_CAROUSEL_MIN_SLIDE_COUNT, int(count))
    evidence = "; ".join(essence.evidence)
    user = _CAROUSEL_PROMPT_USER_TEMPLATE.format(
        n=n,
        core_claim=essence.core_claim,
        mechanism=essence.mechanism,
        evidence=evidence,
        content_mode=essence.content_mode,
        domain=essence.domain,
    )
    raw = await planner_app.ai(
        system=_CAROUSEL_PROMPT_SYSTEM,
        user=user,
        schema=list[str],
    )
    raw_is_single_prompt = isinstance(raw, str)
    if raw_is_single_prompt:
        raw_prompts = [raw]
    else:
        raw_prompts = list(raw or [])
    prompts = []
    for raw_prompt in raw_prompts:
        if not raw_prompt:
            continue
        prompt = raw_prompt.strip()
        if not prompt:
            continue
        prompts.append(prompt)
    return prompts[:n]


def _default_carousel_output_dir(run_id: str) -> Path:
    return Path.cwd() / _CAROUSEL_OUTPUT_ROOT / f"{_CAROUSEL_OUTPUT_DIR_PREFIX}-{run_id}"


def _has_openrouter_api_key() -> bool:
    return "OPENROUTER_API_KEY" in os.environ


async def _render_one_slide(
    *,
    provider,
    storage,
    run_id: str,
    idx: int,
    prompt: str,
    out_dir: Path,
    content_mode: str,
    model: str | None,
    crop: str,
    _generate_frame,
) -> dict:
    path = await _generate_frame(
        provider,
        prompt,
        idx,
        out_dir,
        content_mode,
        model=model,
        crop=crop,
    )
    ref = await storage.put(run_id=run_id, idx=idx, path=path)
    return _slide_record(idx, prompt, ref, _SLIDE_STATUS_OK)


@reel.reasoner()
async def research_to_carousel(
    text: str,
    preset: str = CAROUSEL_DEFAULT_PRESET,
    slide_count: int | None = None,
    model: str | None = None,
    out_dir: str | None = None,
    *,
    provider=None,
    storage=None,
    distiller=None,
    prompt_planner=None,
    _generate_frame=None,
) -> dict:
    """Text/research document to an ordered image carousel."""
    has_openrouter_api_key = _has_openrouter_api_key()
    if not has_openrouter_api_key:
        return {"error": _CAROUSEL_OPENROUTER_ERROR}

    provider = provider or OpenRouterProvider()
    storage = storage or _default_storage_port()
    distiller = distiller or (lambda document: essence_from_text(app, document))
    prompt_planner = prompt_planner or (
        lambda essence, count: globals()["plan_carousel_prompts"](app, essence, count)
    )
    _generate_frame = _generate_frame or generate_first_frame

    run_id = uuid.uuid4().hex[:_CAROUSEL_RUN_ID_HEX_CHARS]
    run_dir = Path(out_dir) if out_dir else _default_carousel_output_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_preset(preset)
    count = max(
        _CAROUSEL_MIN_SLIDE_COUNT,
        int(slide_count if slide_count is not None else cfg["slide_count"]),
    )
    crop = str(cfg.get("crop", CAROUSEL_DEFAULT_CROP))
    essence = await _maybe_await(distiller(text))
    prompts = await _resolve_prompts(prompt_planner, essence, count)
    prompt_count = len(prompts)
    if prompt_count != count:
        raise ValueError(
            _CAROUSEL_PROMPT_COUNT_ERROR_TEMPLATE.format(
                actual=prompt_count,
                expected=count,
            )
        )
    slides = []
    for idx, prompt in enumerate(prompts):
        try:
            slides.append(
                await _render_one_slide(
                    provider=provider,
                    storage=storage,
                    run_id=run_id,
                    idx=idx,
                    prompt=prompt,
                    out_dir=run_dir,
                    content_mode=essence.content_mode,
                    model=model,
                    crop=crop,
                    _generate_frame=_generate_frame,
                )
            )
        except Exception as exc:
            slides.append(
                _slide_record(
                    idx,
                    prompt,
                    None,
                    _SLIDE_STATUS_FAILED,
                    error=str(exc),
                )
            )
    return {
        "run_id": run_id,
        "preset": preset,
        "out_dir": str(run_dir),
        "slides": slides,
    }


async def regenerate_slide(
    *,
    run_id: str,
    idx: int,
    image_prompt: str,
    out_dir: str,
    provider=None,
    storage=None,
    content_mode: str = "general",
    model: str | None = None,
    crop: str = CAROUSEL_DEFAULT_CROP,
    _generate_frame=None,
) -> dict:
    """Regenerate exactly one carousel slide."""
    if idx < 0:
        raise ValueError(_CAROUSEL_NEGATIVE_IDX_ERROR_TEMPLATE.format(idx=idx))
    provider = provider or OpenRouterProvider()
    storage = storage or _default_storage_port()
    _generate_frame = _generate_frame or generate_first_frame
    return await _render_one_slide(
        provider=provider,
        storage=storage,
        run_id=run_id,
        idx=idx,
        prompt=image_prompt,
        out_dir=Path(out_dir),
        content_mode=content_mode,
        model=model,
        crop=crop,
        _generate_frame=_generate_frame,
    )


# ════════════════════════════════════════════════════════════════════
# ENTRY REASONER — Research selection → Reel  (MW Phase 3 B1, contract C6)
# ════════════════════════════════════════════════════════════════════

_RESEARCH_PACKAGE_KEY = "research_package"
_SECTIONS_KEY = "sections"
_SECTION_CONTENT_KEY = "content"
_PARAGRAPH_SEP = "\n\n"


def _research_package_of(record) -> dict | None:
    """The research document nested at ``record.result["research_package"]``.

    Accepts an ``agentfield.handoff`` ``ExecutionRecord``-like object (attribute
    ``result``) or a plain dict; returns ``None`` when absent."""
    result = getattr(record, "result", None)
    if result is None and isinstance(record, dict):
        result = record.get("result")
    if not isinstance(result, dict):
        return None
    pkg = result.get(_RESEARCH_PACKAGE_KEY)
    return pkg if isinstance(pkg, dict) else None


def _paragraph_from_package(pkg: dict | None, paragraph_id) -> str | None:
    """Resolve ``"{sectionIndex}-{paragraphIndex}"`` against the research package:
    ``sections[sectionIndex].content`` split on double-newline (spec §4)."""
    if not pkg or not isinstance(paragraph_id, str) or "-" not in paragraph_id:
        return None
    sec_s, _, par_s = paragraph_id.partition("-")
    try:
        sec_i, par_i = int(sec_s), int(par_s)
    except ValueError:
        return None
    sections = pkg.get(_SECTIONS_KEY) or []
    if not 0 <= sec_i < len(sections):
        return None
    paras = str(sections[sec_i].get(_SECTION_CONTENT_KEY, "")).split(_PARAGRAPH_SEP)
    if not 0 <= par_i < len(paras):
        return None
    return paras[par_i]


def _resolve_selected_text(selected_paragraphs, pkg) -> tuple[str, str | None]:
    """Concatenate the selected paragraph text in document order (sorted by
    ``position``). Prefer the inline ``text`` sent by the DR side; fall back to
    resolving the paragraph id against the fetched research package (source of
    truth). Returns ``(selected_text, error_code_or_None)``."""
    if not selected_paragraphs:
        return "", None
    ordered = sorted(selected_paragraphs, key=lambda p: p.get("position", 0))
    texts: list[str] = []
    for p in ordered:
        text = p.get("text")
        if not text:
            text = _paragraph_from_package(
                pkg, p.get("paragraph_id") or p.get("paragraphId")
            )
        if not text:
            return "", "unknown_paragraph_id"
        texts.append(text)
    return _PARAGRAPH_SEP.join(texts), None


def _default_research_fetch_body(execution_id: str):
    """Production fetch-by-reference seam. Lazily builds an ``agentfield.handoff``
    ``ControlPlaneSource`` from env so importing this module never requires the
    handoff SDK to be installed (tests inject a fake ``fetch_body``)."""
    from agentfield.handoff.control_plane_source import ControlPlaneSource

    base_url = os.getenv("AGENTFIELD_SERVER_URL", "") or os.getenv("AGENTFIELD_SERVER", "")
    source = ControlPlaneSource(base_url, os.getenv("AGENTFIELD_API_KEY", ""))
    return source.fetch_body(execution_id)


async def _default_research_compose(node: str, essence: dict) -> dict:
    """Production compose seam — the REQUIRED essence→script stage (do not skip)."""
    c_out = await app.call(f"{node}.reel_compose_script", essence=essence)
    return c_out["script"]


# MW Phase 3 B4 — the frozen reel.completed contract type (registered in agentfield.handoff).
REEL_COMPLETED_EVENT_TYPE = "com.silmari.reel.completed.v1"


def _announce_reel_completed(dto, *, execution_id, node_id, publisher, announce_fn, logger=None):
    """Best-effort reference-surface announce of ``reel.completed`` (MW Phase 3 B4, Option B).

    The PRODUCTION producer is the CP-side ``BuildReelCompletedOutboxRecord`` (same-tx durable
    outbox); this SDK ``announce`` is the reference/test surface — it validates the DTO against
    the frozen schema and is exercised in tests with a fake ``publisher``. It NEVER fails the
    reel: no publisher wired → skip; ``agentfield.handoff`` absent → skip; any error is logged.
    ``announce_fn`` is injectable so tests exercise the wiring without the SDK installed."""
    if publisher is None:
        return
    registry = None
    if announce_fn is None:
        try:
            from agentfield.handoff import announce as _imported_announce
            from agentfield.handoff import registry as _imported_registry
        except Exception:  # noqa: BLE001 - SDK optional at runtime; reference surface only
            return
        announce_fn = _imported_announce
        registry = _imported_registry
    try:
        announce_fn(
            REEL_COMPLETED_EVENT_TYPE,
            dto,
            execution_id=execution_id,
            node_id=node_id,
            publisher=publisher,
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001 - announce is best-effort; never fail the reel
        if logger is not None:
            logger.note(
                f"reel.completed announce failed (best-effort): {exc}",
                tags=["reel", "announce", "error"],
            )


@reel.reasoner()
async def research_to_reel(
    source_execution_id: str,
    selected_paragraphs: list[dict] | None = None,
    source_run_id: str | None = None,
    source_package_ref: str | None = None,
    citations: list[dict] | None = None,
    out_dir: str | None = None,
    *,
    fetch_body=None,
    distiller=None,
    composer=None,
    renderer=None,
    publisher=None,
    announce_fn=None,
    uploader=None,
) -> dict:
    """Research selection → grounded vertical reel (MW Phase 3 B1, contract C6).

    Text front-door onto the shared video pipeline: fetch the source research
    document BY REFERENCE (single source of truth), ground essence on the
    SELECTED paragraph text (skip URL fetch), compose a script (REQUIRED stage),
    and render via the shared ``_render_downstream``. Provenance (``source_run_id``
    + ``citations``) rides into the reel metadata.

    Mirrors ``article_to_reel`` (compose + render half) and ``research_to_carousel``
    (text front-door via ``essence_from_text``). The four keyword seams
    (``fetch_body`` / ``distiller`` / ``composer`` / ``renderer``) default to
    production and are injected in tests so the behavior runs with no live infra.

    Example:
      curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_research_to_reel \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"source_execution_id":"exec_...","selected_paragraphs":[{"paragraph_id":"0-0","text":"...","position":0}],"source_run_id":"run_..."}}'
    """
    if not _has_openrouter_api_key():
        return {"error": "OPENROUTER_API_KEY not set in env."}

    fetch_body = fetch_body or _default_research_fetch_body
    distiller = distiller or (lambda text: essence_from_text(app, text))
    composer = composer or _default_research_compose
    renderer = renderer or _render_downstream

    node = app.node_id

    # Phase 0 — fetch the research document by reference (source of truth; the
    # red-at-seam boundary). A 404 / unreachable CP fails closed — no partial reel.
    try:
        record = await _maybe_await(fetch_body(source_execution_id))
    except Exception as exc:  # noqa: BLE001 - any fetch failure is a closed failure
        app.note(
            f"reel-af research: fetch_body failed for {source_execution_id}: {exc}",
            tags=["reel", "research", "source_unavailable"],
        )
        return {"error": "source_unavailable", "source_execution_id": source_execution_id}
    if record is None:
        return {"error": "source_unavailable", "source_execution_id": source_execution_id}

    pkg = _research_package_of(record)

    # Phase 0b — resolve the grounding text (selected paragraphs, document order).
    selected_text, resolve_err = _resolve_selected_text(selected_paragraphs, pkg)
    if resolve_err is not None:
        return {"error": resolve_err, "source_execution_id": source_execution_id}
    if not selected_text.strip():
        return {"error": "empty_selection", "source_execution_id": source_execution_id}

    run_id = uuid.uuid4().hex[:8]
    out_path = (
        Path(out_dir) if out_dir else (Path.cwd() / "output" / f"research-{run_id}")
    )
    out_path.mkdir(parents=True, exist_ok=True)
    media_dir = out_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    app.note(
        f"reel-af research: starting run {run_id} from {source_execution_id}",
        tags=["reel", "research", "start"],
    )
    t_pipeline = time.time()

    # Phase 1 — essence, grounded on the SELECTED text (skip URL fetch)
    t = time.time()
    essence_obj = await _maybe_await(distiller(selected_text))
    essence = (
        essence_obj.model_dump() if hasattr(essence_obj, "model_dump") else essence_obj
    )
    timings["extract"] = round(time.time() - t, 1)

    # Phase 2 — compose script (REQUIRED — do NOT skip; see article_to_reel)
    t = time.time()
    script = await _maybe_await(composer(node, essence))
    timings["compose"] = round(time.time() - t, 1)

    # Phase 3 — shared downstream render (audio → beats → visuals → stitch)
    final = await _maybe_await(
        renderer(
            node=node,
            essence=essence,
            script=script,
            out_path=out_path,
            media_dir=media_dir,
            run_id=run_id,
            timings=timings,
        )
    )

    timings["total"] = round(time.time() - t_pipeline, 1)

    # T10: the reel is on the node's ephemeral fs — deliver it out via the shared bucket and
    # hand the browser a presigned GET url (mirror composite_to_reel). Fail-soft: None if the
    # bucket is unset, so the reel still surfaces its local video_path.
    if uploader is None:
        from reel_af.storage import upload_reel as uploader
    filename = reel_output_name(
        essence.get("core_claim") or source_execution_id, run_id, datetime.now(timezone.utc).date()
    )
    download_url = await asyncio.to_thread(
        uploader, final["video_path"], run_id=run_id, filename=filename
    )
    delivered = {"download_url": download_url} if download_url else {}
    app.note(
        f"reel-af research: run {run_id} done → {final['video_path']} "
        f"(delivered={bool(download_url)})",
        tags=["reel", "research", "done"],
    )

    # B4 (Option B): announce reel.completed — SDK reference/test surface. The PRODUCTION
    # producer is the CP-side BuildReelCompletedOutboxRecord (same-tx durable outbox). DTO is
    # the frozen com.silmari.reel.completed.v1 shape; best-effort — never fails the reel.
    reel_dto = {
        "run_id": run_id,
        "status": "succeeded",
        "reel_ref": download_url or final.get("video_path", ""),
        "source_execution_id": source_execution_id,
        "duration_s": final.get("duration_s"),
        "beat_count": final.get("beat_count"),
    }
    _announce_reel_completed(
        reel_dto,
        execution_id=run_id,
        node_id=node,
        publisher=publisher,
        announce_fn=announce_fn,
        logger=app,
    )

    return {
        **final,
        **delivered,
        "source": "research",
        "source_run_id": source_run_id,
        "source_execution_id": source_execution_id,
        "source_package_ref": source_package_ref,
        "citations": citations or [],
        "run_id": run_id,
        "timings_s": timings,
    }


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


# NOTE: `app.include_router(reel)` is intentionally deferred to the BOTTOM of this
# module (after every @reel.reasoner is defined). Mounting it here would snapshot
# the router early and silently drop any reasoner defined below this point — which
# is exactly how Slice A's dsl_hooks_to_reels went unregistered on the agent.


# ─────────────────── A1 DSL-hooks target (Slice A, B16) ───────────────────
#
# Target id derivation: the SDK builds reasoner_id as "<router prefix>_<func
# name>" and the call target as "<node_id>.<reasoner_id>". Router prefix is
# "reel", node is "reel-af" — so this function MUST be named dsl_hooks_to_reels
# to expose "reel-af.reel_dsl_hooks_to_reels". Renaming it silently changes the
# public target id.

DSL_HOOKS_ERROR_ARTIFACT_UNAVAILABLE = "dsl_artifact_unavailable"
DSL_HOOKS_ERROR_INVALID_SOURCE_URL = "invalid_source_url"
DSL_HOOKS_ERROR_COMPILE_FAILED = "dsl_compile_failed"
DSL_HOOKS_ERROR_CUTIN_INVALID = "dsl_cutin_invalid"
DSL_HOOKS_ERROR_RENDER_FAILED = "dsl_render_failed"


def _diag_dicts(diagnostics) -> list[dict]:
    return [
        {"code": d.code, "message": d.message, "severity": d.severity}
        for d in diagnostics
    ]


def _is_browser_deliverable_url(ref: Any) -> bool:
    """Pure question: is this a browser-fetchable http(s) URL with a host?"""
    if not isinstance(ref, str) or not ref:
        return False
    parsed = urlparse(ref)
    return parsed.scheme in BROWSER_DELIVERABLE_SCHEMES and bool(parsed.netloc)


def _default_segment_fetch(request):
    """Production segment fetcher: pull the source span with the crisp downloader.

    Mirrors the `uploader` seam — a production default that tests replace.
    """
    from reel_af.render.video import download_crisp_source

    download_crisp_source(request.source_url, str(request.target_path))
    return request.target_path


# --- A1 artifact resolution -------------------------------------------------
# reel-af runs on Railway (remote), so A1's artifacts are NOT on this node's
# filesystem. Refs arrive as http(s):// (A1-served or presigned bucket URLs — the
# production path, reachable from Railway) or a1://<rel> (co-located dev, mapped
# under $A1_ARTIFACTS_BASE). Bare local paths are for tests/fixtures only;
# reel-af's submit canonicalization forbids them in production.
A1_ARTIFACT_SCHEME = "a1://"
A1_ARTIFACTS_BASE_ENV = "A1_ARTIFACTS_BASE"
_ARTIFACT_FETCH_TIMEOUT_S = 30


def _default_artifact_fetch(url: str) -> bytes:
    """Fetch an artifact over HTTP(S) — the Railway worker pulling an A1-served or
    presigned-bucket URL. Network/HTTP failure is terminal (→ dsl_artifact_unavailable)."""
    import requests

    try:
        resp = requests.get(url, timeout=_ARTIFACT_FETCH_TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OSError(f"artifact fetch failed: {url}: {exc}") from exc
    return resp.content


def _resolve_artifact_ref(ref: str, dest_dir: Path, name: str, fetch) -> Path:
    """Resolve an A1 artifact ref to a local readable path.

    - ``http(s)://`` → fetch bytes into ``dest_dir/name`` (the Railway production path;
      covers A1-served URLs and presigned shared-bucket URLs).
    - ``a1://<rel>`` → ``$A1_ARTIFACTS_BASE/<rel>`` (co-located dev only; unset base
      is terminal).
    - otherwise → treat as a local path (tests/fixtures).

    Raises OSError/ValueError on an unresolvable ref (mapped to dsl_artifact_unavailable).
    """
    parsed = urlparse(ref) if isinstance(ref, str) else urlparse("")
    if parsed.scheme in BROWSER_DELIVERABLE_SCHEMES and parsed.netloc:
        dest = dest_dir / name
        dest.write_bytes(fetch(ref))
        return dest
    if isinstance(ref, str) and ref.startswith(A1_ARTIFACT_SCHEME):
        base = os.getenv(A1_ARTIFACTS_BASE_ENV)
        if not base:
            raise ValueError(
                f"a1:// artifact ref but {A1_ARTIFACTS_BASE_ENV} is unset: {ref}"
            )
        return Path(base) / ref[len(A1_ARTIFACT_SCHEME) :]
    return Path(ref)


def _load_hook_clip(hook_ref: str, clip_idx: int) -> dict:
    """Read one clip out of an A1 hook-plan.json v1 artifact."""
    plan = json.loads(Path(hook_ref).read_text(encoding="utf-8"))
    for clip in plan.get("clips", []):
        if clip.get("idx") == clip_idx:
            return clip
    raise FileNotFoundError(f"hook plan has no clip_idx={clip_idx}")


@reel.reasoner()
async def dsl_hooks_to_reels(
    source_url: str,
    composite_ref: str,
    words_ref: str,
    hook_ref: str,
    clip_idx: int = 1,
    out_dir: str | None = None,
    *,
    fetch_segment=None,
    uploader=None,
    text_provider=None,
    image_provider=None,
    artifact_fetch=None,
) -> dict:
    """A1 DSL hook clip → real-footage vertical reel → browser-deliverable URL.

    The A1 artifact set (composite.ts.md + transcript.words.json + hook-plan.json)
    is the ONLY input: no article readability, no topic generation, no clip-plan.

    Worker contract (research steps 1-11): read composite/words → parse markers →
    resolve/align → compile with CompileContext → reject compile errors → validate
    renderability → map cut-ins (B9a: validated, NOT rendered — B9b deferred) →
    download segments → stitch 1080x1920 → finish (banner/captions/image cut-ins,
    no raw opt-out) → deliver.

    Delivery is REQUIRED: unlike the other reasoners, a missing browser URL is
    terminal ``delivery_unavailable``. A node-local ``video_path`` is never
    presented as success and never returned.

    Example:
      curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_dsl_hooks_to_reels \\
        -H 'Content-Type: application/json' \\
        -d '{"input":{"source_url":"https://www.youtube.com/watch?v=abc123","composite_ref":"a1://.../composite.ts.md","words_ref":"a1://.../transcript.words.json","hook_ref":"a1://.../hook-plan.json","clip_idx":1}}'
    """
    fetch_segment = fetch_segment or _default_segment_fetch
    if uploader is None:
        from reel_af.storage import upload_reel as uploader
    text_provider = text_provider or app
    image_provider = image_provider if image_provider is not None else _media_provider()
    artifact_fetch = artifact_fetch or _default_artifact_fetch

    run_id = uuid.uuid4().hex[:12]
    work = Path(out_dir) if out_dir else Path(f"/tmp/reel-af/dsl-hooks/{run_id}")
    work.mkdir(parents=True, exist_ok=True)

    # Guard: reject a non-HTTP(S) source before ANY artifact read or side effect.
    if not _is_browser_deliverable_url(source_url):
        return {"error": DSL_HOOKS_ERROR_INVALID_SOURCE_URL, "source_url": source_url}

    # Steps 1-2 — resolve refs (remote-fetch for the Railway worker), then load the
    # A1 artifacts. Unresolvable/missing/unreadable is terminal, pre-render.
    try:
        composite_path = _resolve_artifact_ref(composite_ref, work, "composite.ts.md", artifact_fetch)
        words_path = _resolve_artifact_ref(words_ref, work, "words.json", artifact_fetch)
        hook_path = _resolve_artifact_ref(hook_ref, work, "hook-plan.json", artifact_fetch)
        doc = read_composite_file(composite_path)
        words = load_words(words_path)
        clip = _load_hook_clip(str(hook_path), clip_idx)
    except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
        return {"error": DSL_HOOKS_ERROR_ARTIFACT_UNAVAILABLE, "detail": str(exc)}

    # Steps 3-5 — parse/resolve/align/compile. CompileContext supplies what the
    # .ts.md and words sidecar cannot (research C27).
    context = CompileContext(
        workflow=DSL_HOOKS_WORKFLOW,
        source_url=source_url,
        video_id=clip.get("source_id") or None,
        delivery_required=True,
        cut_ins=[CutInSpec.model_validate(c) for c in clip.get("cut_ins", [])]
        if _cut_ins_are_wellformed(clip.get("cut_ins", []))
        else [],
    )
    result = compile_composite(
        doc, words, SourceRef(source_url=source_url), context=context
    )

    # Step 6 — guard clause BEFORE any render side effect.
    if result.status == "error" or result.plan is None:
        return {
            "error": DSL_HOOKS_ERROR_COMPILE_FAILED,
            "diagnostics": _diag_dicts(result.diagnostics),
        }

    # Step 7 — renderability postconditions stronger than the schema.
    try:
        validate_renderable(result.plan)
    except RenderabilityError as exc:
        return {"error": DSL_HOOKS_ERROR_COMPILE_FAILED, "detail": str(exc),
                "diagnostics": _diag_dicts(result.diagnostics)}

    # B9a — map/validate A1 cut-ins. An unanchored cut-in fails closed rather than
    # being silently dropped. Slice A does NOT render them (B9b deferred).
    cut_ins, cut_in_diags = map_cut_ins(clip.get("cut_ins", []), reel=result.plan)
    if cut_in_diags:
        return {
            "error": DSL_HOOKS_ERROR_CUTIN_INVALID,
            "diagnostics": _diag_dicts(cut_in_diags),
        }

    # Steps 8-10 — fetch real footage, stitch vertical, finish by default.
    try:
        assets = await asyncio.to_thread(
            download_segments, result.plan, work / "segments", fetch_segment
        )
        base = await stitch_footage_reel(
            result.plan, assets, work / "base", run_id=run_id
        )
        transcript = " ".join(s.text for s in result.plan.segments
                              if getattr(s, "kind", None) == "source")
        final = await finish_reel(
            base,
            FinishContext(transcript=transcript, text_provider=text_provider,
                          image_provider=image_provider, source_url=source_url,
                          run_id=run_id),
            cfg=_finish_config_for(image_provider),
            out_dir=work / "final",
            raw=False,  # no raw/fast opt-out on this workflow
        )
    except Exception as exc:  # noqa: BLE001 — reasoners return errors, never raise
        return {"error": DSL_HOOKS_ERROR_RENDER_FAILED, "detail": str(exc)}

    # Step 11 — delivery is REQUIRED. Missing/non-browser-deliverable is terminal.
    filename = reel_output_name(clip.get("title") or clip.get("hook"), run_id,
                                datetime.now(timezone.utc).date())
    download_url = await asyncio.to_thread(
        uploader, str(final), run_id=run_id, filename=filename
    )
    if not _is_browser_deliverable_url(download_url):
        app.note(
            f"reel-af dsl-hooks: run {run_id} produced a reel with no browser-deliverable "
            f"URL — terminal {A1_DELIVERY_UNAVAILABLE}",
            tags=["reel", "dsl-hooks", "delivery"],
        )
        return {"error": A1_DELIVERY_UNAVAILABLE, "run_id": run_id}

    return {
        "download_url": download_url,
        "run_id": run_id,
        "target_workflow": DSL_HOOKS_WORKFLOW,
        "clip_idx": clip_idx,
        "segment_count": len(result.plan.segments),
        "cut_in_count": len(cut_ins),
        "duration_s": result.plan.duration_s,
        "source": "dsl_hooks",
    }


def _cut_ins_are_wellformed(raw_cut_ins: list) -> bool:
    """Pure question: can every raw cut-in be typed? Malformed ones are surfaced
    by map_cut_ins as CUTIN_INVALID rather than exploding CompileContext."""
    try:
        for cut_in in raw_cut_ins:
            CutInSpec.model_validate(cut_in)
    except Exception:  # noqa: BLE001
        return False
    return True


def _media_provider():
    try:
        return OpenRouterProvider()
    except Exception:  # noqa: BLE001 — image cut-ins degrade, they don't fail the reel
        return None


def _finish_config_for(image_provider: Any) -> ReelFinishConfig:
    """Finish config for the DSL-hooks workflow.

    Guard: image cut-ins REQUIRE an image provider — ``generate_image_cutins``
    calls ``provider.generate_image`` unconditionally. With no provider,
    ``image_count=0`` degrades cut-ins out rather than crashing the whole reel.
    The hook banner and safe-zone captions still burn, so the reel stays valid.
    There is no raw/fast opt-out on this workflow either way.
    """

    cfg = ReelFinishConfig()
    if image_provider is None:
        return cfg.model_copy(update={"image_count": 0})
    return cfg


def _health() -> dict:
    return {"status": "ok", "service": "reel-af", "version": "1.0.0"}


# Mount the reel router AFTER every @reel.reasoner above (incl. dsl_hooks_to_reels)
# so ALL reasoners propagate into the served app and the control-plane registration.
app.include_router(reel)


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
