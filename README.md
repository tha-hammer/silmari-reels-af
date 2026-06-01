<div align="center">

# REELS-AF

### AI-Native Viral Reel Producer Built on [AgentField](https://github.com/Agent-Field/agentfield)

[![Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-16a34a?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Built with AgentField](https://img.shields.io/badge/Built%20with-AgentField-0A66C2?style=for-the-badge)](https://github.com/Agent-Field/agentfield)
[![OpenRouter](https://img.shields.io/badge/Powered%20by-OpenRouter-FF6B35?style=for-the-badge)](https://openrouter.ai/)
[![More from Agent-Field](https://img.shields.io/badge/More_from-Agent--Field-111827?style=for-the-badge&logo=github)](https://github.com/Agent-Field)

<p>
  <a href="#sample-reels">Sample Reels</a> •
  <a href="#see-it-run">See It Run</a> •
  <a href="#cost-and-timing">Cost</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#run-it-yourself">Quick Start</a> •
  <a href="#customize">Customize</a>
</p>

</div>

Article URL or topic phrase → 1080×1920 vertical reel with word-burst karaoke, in about 80 seconds at **≈$0.10 per reel**. Open source, one API call, OpenRouter-only — Gemini 3.1 Flash TTS + Gemini 2.5 Flash Image + ken-burns motion. Flip on Veo 3.1 Lite i2v for full motion at ≈$1.20.

<p align="center">
  <img src="assets/hero.png" alt="reels-af: every URL, every topic, a reel" width="100%" />
</p>

## One-Call DX

Trigger it with the `af` CLI (requires af ≥ 0.1.86) — it streams live progress and prints the result:

```bash
# URL → reel
af call reel-af.reel_article_to_reel --in '{"url": "https://arxiv.org/abs/2509.25541"}'

# Topic → reel (runs the 4-hunter → critic → 3-narrator → judge cascade)
af call reel-af.reel_topic_to_reel --in '{"topic": "the placebo effect"}'
```

Prefer raw HTTP? Hit the API directly with curl:

```bash
# URL → reel
curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_article_to_reel \
  -H "Content-Type: application/json" \
  -d '{"input": {"url": "https://arxiv.org/abs/2509.25541"}}'

# Topic → reel (runs the 4-hunter → critic → 3-narrator → judge cascade)
curl -X POST http://localhost:8080/api/v1/execute/async/reel-af.reel_topic_to_reel \
  -H "Content-Type: application/json" \
  -d '{"input": {"topic": "the placebo effect"}}'
```

Other pipelines fight TTS sync drift, over-pause and kill retention, generate literal-but-boring visuals, or front-load the hook with no curiosity gap. **reels-af** is the multi-reasoner answer: 18 specialized reasoners run through the AgentField control plane to extract the essence, write a Hook→Mechanism→Payoff script, hunt a viral angle (topic mode), synthesize sample-accurate audio, plan beats and cards in parallel, generate per-beat first frames + motion, and stitch everything in a single ffmpeg pass.

Two entry points, one downstream pipeline. Drop in a URL when you have a source. Drop in a topic when you just have a thread to pull on.

---

## What you get

**Every reel:**

- **`reel.mp4`** — 1080×1920 vertical, 20-25s, H.264 + AAC, ready to upload
- **Word-burst karaoke** — one word at a time, 170px bottom-center, sample-accurate
- **Per-beat first frames** — Gemini Flash Image stills, content-mode-aware style
- **Motion** — ken-burns by default (free); Veo 3.1 Lite i2v when `REEL_AF_USE_VEO=true`
- **Optional editorial accents** — UPPERCASE callouts for numbers, names, jargon glosses
- **`result.json`** — hook variant, beats, voice, hunter rankings, judge verdict, per-phase timings

**Topic runs also produce:** 12 candidate essences (4 hunters × 3), critic rankings on novelty/specificity/hookability/narratability, 3 narrator drafts, the pairwise judge verdict.

Sample sidecar:

```json
{
  "source": "topic",
  "topic": "fingerprints",
  "video_path": "output/topic-2ec74c00/reel.mp4",
  "duration_s": 22.4,
  "tease": "Why do we have fingerprints?",
  "reveal": "In 2009, a biomechanics study led by Georges Debrégeas found that fingerprints reduce friction on smooth surfaces. They channel moisture away like tire treads — but their real purpose is to amplify vibrations our fingertips' hyper-sensitive touch receptors can feel.",
  "payoff": "Fingerprints aren't for holding on. They're for feeling.",
  "open_style": "question",
  "chosen_essence": {
    "core_claim": "Fingerprints amplify vibrations to enable hyper-sensitive touch perception, not grip",
    "angle": "specific_figure",
    "novelty_pitch": "Most assume fingerprints exist for grip — the 2009 Debrégeas paper showed they're actually a vibration-amplification system for touch sensing"
  },
  "winner_composite": 8.4,
  "winner_why": "Specific named researcher + counter-intuitive reversal + clean payoff that callbacks the tease.",
  "beat_count": 4,
  "card_count": 18,
  "accent_count": 2,
  "timings_s": {
    "hunt": 8.1, "critic": 4.2, "narrate": 7.5, "judge": 3.1,
    "tts": 12.4, "plan": 1.1, "visual_accent": 6.8, "media": 38.2, "stitch": 4.6,
    "total": 86.0
  }
}
```

---

## Sample reels

Three reels, each from a single function call.

<table>
<tr>
<td width="33%" align="center">
<video src="https://github.com/user-attachments/assets/c8d8f307-1472-4766-b99c-35eadcb61182" autoplay loop muted playsinline width="100%"></video>
<br><b>topic → "fingerprints"</b><br>
<sub>Hunter cascade landed on the 2009 Debrégeas paper. Delayed-reveal — answer arrives at beat 2.</sub>
</td>
<td width="33%" align="center">
<video src="https://github.com/user-attachments/assets/f001f306-4fee-44ff-be4f-9293098ec8c7" autoplay loop muted playsinline width="100%"></video>
<br><b>topic → "placebo effect"</b><br>
<sub>Specific-figure angle won the critic round: Ted Kaptchuk's 2010 open-label IBS study.</sub>
</td>
<td width="33%" align="center">
<video src="https://github.com/user-attachments/assets/463f3744-43d4-4ba8-bbd9-a3d967f8ec03" autoplay loop muted playsinline width="100%"></video>
<br><b>article → arXiv paper</b><br>
<sub>Scientific mode auto-activated — tighter pacing (175 WPM), paper-specific terms defined inline.</sub>
</td>
</tr>
</table>

---

## See it run

<video src="https://github.com/user-attachments/assets/e99c8c31-12ec-49cb-a00a-dfa212a7af54" autoplay loop muted playsinline width="100%"></video>

The AgentField control plane rendering the 18-reasoner DAG live. Each node is one reasoner — its prompt, inputs, outputs, latency, cost. A single `topic_to_reel` invocation lights up the 4-hunter fan, the critic, the 3-narrator fan, the judge, then the shared downstream — about 80 seconds end-to-end.

---

## What powers it

| Layer | Tool | What it brings |
|---|---|---|
| **Runtime** | [AgentField](https://github.com/Agent-Field/agentfield) | Async-parallel reasoner orchestration. 18 reasoners per reel; depth-3 DAG (4 hunters → critic → 3 narrators → judge → 6 downstream phases); every node visible in the workflow graph. |
| **Reasoning** | [OpenRouter](https://openrouter.ai/) → DeepSeek V4 Pro | One env var swaps the whole stack to any OpenRouter model. |
| **TTS** | Gemini 3.1 Flash TTS | 200+ inline audio tags for delivery direction. Sentence-by-sentence in parallel, ffprobe-measured, `atempo=1.35` sped, native-wave concatenated → sample-accurate sentence boundaries. |
| **Image** | Gemini 2.5 Flash Image | One 720×1280 first frame per beat, content-mode-aware style. |
| **Motion** | ffmpeg `zoompan` (default) / Veo 3.1 Lite i2v (`REEL_AF_USE_VEO=true`) | Ken-burns animation of the still is free and ships by default. Flip the env var to Veo for real i2v motion at ~$0.05/sec of video. |
| **Subtitles** | libass + pysubs2 | Word-burst (one word at a time, 170px, bottom-center) + optional Layer-2 accent overlays in the opposite third of the frame. |
| **Stitch** | ffmpeg `concat` filter (single pass) | concat + libass burn + AAC mux in one invocation. Sample-accurate; no per-shot priming drift. |

---

## Cost and timing

Default config — ken-burns motion from generated first frames, OpenRouter list prices verified 2026-05:

| Path | Reasoners | Wall time | Cost / reel |
|---|---|---|---|
| `article_to_reel` (URL → reel) | 10 | ~70-90s  | **~$0.08** |
| `topic_to_reel` (topic → reel) | 18 | ~85-110s | **~$0.10** |

The topic path is slightly slower and slightly pricier because of the 4-hunter → critic → 3-narrator → judge cascade. Cost split per reel:

| Stage | Pricing (OpenRouter list) | Cost / reel |
|---|---|---|
| Gemini 2.5 Flash Image (first frames) | $0.30/M in, $2.50/M out | ~$0.02 |
| Gemini 3.1 Flash TTS (4-6 sentences) | $1/M in, $20/M out | ~$0.015 |
| DeepSeek V4 Pro reasoning (10-18 calls) | $0.435/M in, $0.87/M out | ~$0.02 |
| Ken-burns motion (local ffmpeg) | free | $0 |

**Upgrade to full Veo i2v motion** by setting `REEL_AF_USE_VEO=true`. Veo 3.1 Lite at $0.05/sec adds ~$1.10/reel (5 beats × ~6s of generated video), bringing the total to ~$1.20/reel. Worth it for premium output; the ken-burns default is fine for high-volume.

Track actual numbers via the [OpenRouter activity dashboard](https://openrouter.ai/activity) and the `timings_s` block in `result.json`.

---

## How it works

![reels-af 6-phase two-path multi-reasoner pipeline](assets/architecture.png)

Two paths, six phases. Both converge on the same downstream after phase 02.

1. **Intake** — `article_to_reel` runs one harness call to extract the surprising claim + mechanism + evidence + content_mode. `topic_to_reel` fans out **four hunters** (specific_figure / reversal / temporal / cross_domain) → 12 candidates → critic picks the top 3 → 3 narrators write delayed-reveal scripts → **pairwise judge** picks the winner.
2. **Script** — one `.ai()` call produces a ScriptDraft (Hook → Mechanism → Payoff + inline TTS tags). A schema validator enforces the final clause to echo a hook keyword, creating the loop.
3. **Audio** — sentences synthesize in parallel, are ffprobe-measured, sped via `atempo=1.35`, then native-wave concatenated. Sentence boundaries are sample-accurate; words inside are distributed by syllable count. No ASR in the loop.
4. **Plan** — two parallel deterministic helpers (cards for subtitle layout, beats for visual planning) and two parallel LLM fan-outs (per-beat image prompts, per-beat optional accents). Cards and beats render onto the same reel but don't gate each other — no shot-too-long failures.
5. **Render** — one first-frame image per beat (Gemini Flash Image), then either local ken-burns animation (default) or a Veo i2v call per beat (`REEL_AF_USE_VEO=true`). Per-beat fallback: image fail → placeholder; Veo fail → ken-burns.
6. **Stitch** — one ffmpeg invocation: concat filter (sample-accurate) + libass burn (word-burst + accents) + AAC mux of the full TTS WAV. One encode, no priming drift.

The architectural choice that earns the engagement: **video is decoupled from word timing**. Cards drive subtitles, beats drive visuals, audio is master.

---

## Run it yourself

```bash
git clone https://github.com/Agent-Field/reels-af
cd reels-af
cp .env.example .env       # paste OPENROUTER_API_KEY
docker compose up --build
```

Open **http://localhost:8080/ui/** to watch the 18-reasoner DAG execute live. Fire either of the curls from [One-Call DX](#one-call-dx) above — outputs land under `./output/<run-id>/reel.mp4` with a `result.json` sidecar.

Without Docker — local CLI:

```bash
uv sync
brew install ffmpeg            # macOS  (apt install ffmpeg fonts-montserrat on Linux)
cp .env.example .env

reel-af article "https://arxiv.org/abs/2509.25541"
reel-af topic   "the placebo effect"
```

Swap any model or flip on Veo motion with one env var:

```bash
REEL_AF_USE_VEO=true docker compose up --build
REEL_AF_MODEL=openrouter/anthropic/claude-sonnet-4 docker compose up --build
```

---

## Try a few

```bash
# Topics — the hunter cascade finds an angle
reel-af topic "the placebo effect"
reel-af topic "the dark forest hypothesis"
reel-af topic "octopus cognition"
reel-af topic "the Antikythera mechanism"

# Articles — direct from the source
reel-af article "https://arxiv.org/abs/2509.25541"
reel-af article "https://en.wikipedia.org/wiki/Tardigrade"
```

---

## Customize

Most behaviour is driven by environment variables; see [.env.example](./.env.example) for the full list.

| Env var | Default | What it controls |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required. Get one at [openrouter.ai](https://openrouter.ai/) and load $5+ in credits (~50 reels at default config). |
| `REEL_AF_USE_VEO` | `false` | Set to `true` for Veo 3.1 Lite i2v motion (~$1.10 extra per reel). Default ken-burns mode animates the generated stills locally. |
| `REEL_AF_MODEL` | `openrouter/deepseek/deepseek-v4-pro` | Reasoning model for every `.ai()` call. Any OpenRouter model works. |
| `REEL_AF_TTS_MODEL` | `google/gemini-3.1-flash-tts-preview` | TTS model. Gemini Flash is the only one supporting inline audio tags. |
| `REEL_AF_IMAGE_MODEL` | `openrouter/google/gemini-2.5-flash-image` | First-frame image generator. Swap for Flux, Imagen, etc. |
| `REEL_AF_VIDEO_MODEL` | `openrouter/google/veo-3.1-lite` | Veo model used when `REEL_AF_USE_VEO=true`. |
| `AGENT_NODE_ID` | `reel-af` | Node id registered with the AgentField control plane. |
| `AGENTFIELD_SERVER` | `http://localhost:8080` | Control-plane URL (Docker compose wires this automatically). |
| `AGENTFIELD_LLM_CALL_TIMEOUT` | `120` | Per-call timeout in seconds. |

Voice, pacing, and tone are picked in code (`render/tts.py:_VOICE_BY_TONE` and the `_AUDIO_SPEED_FACTOR` constant). Edit those to dial the delivery.

---

## Troubleshooting

**"OPENROUTER_API_KEY not set in env."** — paste your key into `.env`. The Docker container reads it via `docker-compose.yml`; the CLI reads it via `python-dotenv`.

**"ffmpeg / ffprobe not found on PATH."** — `brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux. The Docker build already includes it.

**Subtitles look like sans-serif blocks instead of Montserrat.** — install Montserrat Bold (`brew install --cask font-montserrat` on macOS, `apt install fonts-montserrat` on Linux). The renderer falls back to DejaVu Sans Bold if Montserrat isn't found.

**A single beat's video came out as a still ken-burns instead of motion.** — Veo i2v hit a content-moderation false positive or transient error on that beat. The pipeline's two-tier fallback rendered the beat as a still + slow zoom so the reel still assembles. Re-run that beat by re-running the whole reel, or accept the fallback (often visually fine).

**Reel runs longer than 25 seconds.** — Gemini occasionally honors a stray `[pause]` or punctuation cluster too literally. Check `result.json.timings_s.tts`; if it's >20s, the narration likely picked up an extra tag. Re-run — temperature variance usually resolves it.

**Custom font:** drop a `.ttf` somewhere libass can see and update the candidate list in `render/stitch.py:_FONT_CANDIDATES`.

---

## Features

Shipped:

- [x] Two entry reasoners — `article_to_reel`, `topic_to_reel`
- [x] 4-hunter angle-constrained essence generation + critic + pairwise judge
- [x] Delayed-reveal narration (tease → common_belief → reveal → payoff) with schema-level loop-back validation
- [x] Sample-accurate sentence-by-sentence TTS — no ASR, no Whisper
- [x] Word-burst karaoke (170px, bottom-center, libass)
- [x] Optional editorial accents — 6 patterns (number / named_entity / jargon_translation / hook_title_card / reaction / list_marker)
- [x] Ken-burns motion default + Veo 3.1 Lite i2v upgrade via env var
- [x] Two-tier per-beat fallback (image fail → placeholder; Veo fail → ken-burns)
- [x] Single-pass ffmpeg stitch (concat + libass + AAC)
- [x] Content-mode style switch (cinematic-doc / clinical-lab)
- [x] Docker compose stack with the AgentField control plane bundled
- [x] OpenRouter-only — no Whisper, no local models, no platform lock-in

In progress:

- [ ] Voice cloning via OpenRouter-compatible TTS providers
- [ ] B-roll insertion from a stock-footage retriever
- [ ] Multi-language output (auto-translated script + native-voice TTS)
- [ ] Real-time preview while reasoners are running
- [ ] Direct publish to TikTok / Reels / Shorts via Buffer-style API

---

## Acknowledgments

Built on the open-source work of:

- **[AgentField](https://github.com/Agent-Field/agentfield)**: async-parallel multi-reasoner runtime
- **[OpenRouter](https://openrouter.ai/)**: single endpoint for the entire model stack (reasoning + TTS + image + video)
- **[Google DeepMind](https://deepmind.google/)**: Gemini 3.1 Flash TTS, Gemini 2.5 Flash Image, Veo 3.1 Lite
- **[DeepSeek](https://www.deepseek.com/)**: DeepSeek V4 Pro reasoning model
- **[libass](https://github.com/libass/libass)** + **[pysubs2](https://github.com/tkarabela/pysubs2)**: industry-standard ASS subtitle rendering
- **[FFmpeg](https://ffmpeg.org/)**: the single-pass stitch engine
- **[readability-lxml](https://github.com/buriy/python-readability)**: clean article extraction
- **[Montserrat](https://github.com/JulietaUla/Montserrat)**: the karaoke typeface

---

## License

Apache License 2.0 — see [LICENSE](./LICENSE).

---

### Other projects on AgentField

- [SEC-AF](https://github.com/Agent-Field/sec-af): AI-native security auditor
- [PR-AF](https://github.com/Agent-Field/pr-af): agentic PR reviewer
- [Contract-AF](https://github.com/Agent-Field/contract-af): legal contract risk analyzer
- [Roboscribe-AF](https://github.com/Agent-Field/roboscribe-af): multi-agent annotation for robotic demonstrations
- [Reactive-Atlas](https://github.com/Agent-Field/reactive-atlas): MongoDB to AI enrichment pipeline
