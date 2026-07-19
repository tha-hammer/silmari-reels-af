---
date: 2026-07-19T11:44:37-04:00
researcher: tha-hammer
git_commit: 5f521539b7ea218087a6b9234f2e8bc7ae6c7901
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "reel-af A1 Producer — enhance BAML mine/strategize/arrange (script quality)"
tags: [implementation, strategy, reel-af, planner, baml, mine, strategize, arrange, script-quality, retention]
status: complete
last_updated: 2026-07-19
last_updated_by: tha-hammer
type: implementation_strategy
---

# Handoff: reel-af A1 producer end-to-end WORKS; next = make the BAML script good

## Task(s)

1. **Build the A1-producer BAML backend (S0–S3)** — **DONE, committed `5f52153`.** Self-contained BAML
   install; `PlannerLLM` runs mine/strategize/arrange through the BAML runtime (OpenRouter,
   `claude-sonnet-5`). 362 tests green (`tests/dsl` + `tests/planner`).
2. **Fix the `ArrangeReel` argument-coercion bug** — **DONE, verified live.** Single-source refactor:
   `planner/models.py` is a thin facade over `baml_client.types`; field `template_` with **no
   `@alias`** (the alias was breaking function-arg coercion).
3. **DSL reordered-segments + AF-e1x** (relax chronological order; keep injectivity; generalize the
   overlap clamp to source-time interval normalization; add `SOURCE_TIME_OVERLAP`) —
   **RESEARCHED → system_map → REVIEWED → ENHANCED → IMPLEMENTED** via a 4-step fresh-context ntm
   pipeline. **DONE.**
4. **Render the real mp4 end-to-end** — **DONE, verified** (1080×1920 h264, 41.58s, 11.59 MB).
5. **← THIS HANDOFF'S NEXT WORK: enhance BAML mine/strategize/arrange — the generated SCRIPT is
   bad.** The plumbing (transcription → plan → compile → stitch → mp4) works **technically** end to
   end; the *creative* output (which spans get mined, the chosen strategy, the beat arrangement) is
   weak and needs to be much better. **NOT STARTED.**

## Critical References
- **Spec:** `specs/reels-planner.a1-producer.spec.md` — §6 retention rules R1–R12, §12/§13 BAML.
- **The prompts that ARE the script quality:** `baml_src/mine.baml`, `baml_src/strategize.baml`,
  `baml_src/arrange.baml`, and the shared `template_string RetentionRules()` in `baml_src/retention.baml`.
- **Backend plan (done):** `thoughts/searchable/shared/plans/2026-07-18-17-21-tdd-reel-af-planner-baml-backend.md`.

## Recent changes
- `baml_src/{generators,clients,types,retention,mine,strategize,arrange}.baml` — authored; `src/baml_client/**` generated + committed.
- `src/reel_af/planner/llm.py` — `BamlPlannerLLM.mine/strategize/arrange` return BAML objects directly and pass them straight between calls (no pydantic round-trip).
- `src/reel_af/planner/models.py` — facade re-exporting `baml_client.types` + helpers `interrupt_marker`, `validate_candidate_span`, `validate_cut_in`, `validate_interrupt`, `_enum_value`.
- `baml_src/types.baml:154,162` — `template_ Template` (NO `@alias`) — the arrange-bug fix.
- `src/reel_af/planner/verbatim.py` — `enforce_verbatim(...)` requires quotes align ≥ `MATCH_QUALITY_FLOOR` (0.85).
- `src/reel_af/planner/transcribe.py` — OpenRouter `/audio/transcriptions` + ASR fallback chain + forced-align shim; wired into `ingest.transcribe`.
- `src/reel_af/planner/lint.py` — retention lint R1/R2/R3/R4/R8/R11/R12 (`lint_blueprint`).
- `src/reel_af/dsl/compile.py:298-351` — relaxed monotonicity in `_verify_injective_spans`; `_clamp_contiguous_spans` → source-time interval normalization; new `SOURCE_TIME_OVERLAP` guard (`dsl/models.py`).
- `src/reel_af/render/config/planner.json` — `model=anthropic/claude-sonnet-5`, `verbatim_floor`, `remote_asr_chain`.

## Learnings
- **BAML hard constraints (verified via docs + compiler):** enum values MUST be PascalCase (lowercase
  rejected); `template` is a **reserved field keyword** → must be `template_`; a field `@alias` only
  affects LLM prompt/parse, **NOT function-argument coercion** — passing an aliased BAML object back
  into a BAML function fails ("missing required field"). That was the whole `ArrangeReel` bug.
- **Stubbed tests are blind to real BAML coercion.** The suite was 97/362 green while the live e2e
  still failed at arrange — only a **real key-gated run** exposed it. **Corollary for script work:
  quality can only be judged on REAL runs, never on Fakes/stubs.**
- **The verbatim constraint bounds the script.** The LLM may only SELECT and ORDER *verbatim*
  transcript spans (aligner floor 0.85); it cannot rewrite/paraphrase. So a weak script is partly
  because good narration must already exist as contiguous quotable spans. **A key design question for
  the next agent:** is light rewriting / span-joining allowed, or is pure-verbatim a hard rule?
- **Where quality lives:** `mine.baml` (span selection + value/emotion scoring), `strategize.baml`
  (template + hook type + length), `arrange.baml` (beat order, R3 escalation, R8 loop tie-back,
  interrupt placement), `retention.baml` (the shared R1–R12 frame). There are currently **no few-shot
  exemplars** in any phase.
- **Concrete example to study:** the `wPcKNuUG3NM` run — mine returned 14 candidates, strategize chose
  `problem_agitate_solve` + `pain_point` hook, arrange made a 6-beat 41.58s reel. **Read the actual
  `composite.ts.md` (below) to see exactly why the script is mediocre** (span choice + ordering).

## Artifacts
- **Output triple + rendered reel:** `~/reel-af-a1-output/composite.ts.md`, `hook-plan.json`,
  `transcript.words.json`, `pay-now-or-pay-later-20260719-fb2f9882b1f4.mp4` (the actual video).
- **BAML prompts (edit these):** `baml_src/mine.baml`, `strategize.baml`, `arrange.baml`, `retention.baml`.
- **Plans:** `plans/2026-07-18-17-21-tdd-reel-af-planner-baml-backend.md`,
  `plans/2026-07-19-baml-single-source-refactor.md`,
  `plans/2026-07-19-09-48-AF-e1x-tdd-dsl-reordered-segments.md` (+ `-REVIEW.md`).
- **Research:** `research/2026-07-18-reel-af-planner-llm-backend.md`,
  `research/2026-07-19-09-48-AF-e1x-dsl-reordered-segments.md`.
- **Real-run drivers (reusable):** session scratchpad `e2e_driver.py` (URL→plan) and `render_driver.py`
  (triple→mp4). Both load `.env` and hit real OpenRouter.

## Action Items & Next Steps
1. **Read the produced `~/reel-af-a1-output/composite.ts.md` + `hook-plan.json` first** — see the actual
   weak script before changing prompts.
2. **Enhance `mine.baml`** — sharper span selection: higher bar for `value_score`, reject filler,
   prioritize hook-worthy + payoff-worthy spans; better `emotion` tagging.
3. **Enhance `strategize.baml`** — smarter template + hook-type choice; tighter length band; ensure
   hook promise matches payoff (R6).
4. **Enhance `arrange.baml`** — this is where narrative punch is: beat ordering, R3 pacing escalation,
   R8 loop tie-back, one strong R9 share cue, interrupt placement.
5. **Add few-shot exemplars** to each phase (none exist today) + tighten `RetentionRules()`.
6. **Resolve the verbatim-vs-rewrite question** with the principal (see Learnings) — it caps quality.
7. **Build/‌use the eval harness (bead `AF-7q8`)** — there is NO way to *measure* script quality yet
   (only correctness gates: verbatim/lint/compile). An LLM-judge over R1–R12 + a small video corpus is
   needed to iterate objectively.
8. **Iterate on REAL runs** (`OPENROUTER_API_KEY` in `.env`), using `e2e_driver.py` then
   `render_driver.py` — not stubs.

## Other Notes
- **Beads:** `AF-4qg` (BAML backend feature — done, can close), `AF-7q8` (P2 — reel-quality eval
  harness, open, directly relevant to this next work), `AF-e1x` (DSL overlap — fix implemented this
  session; verify + close), `AF-9li` (related, surfaced in DSL research), `AF-7sr` (P2 UI config,
  in_progress, unrelated). **No `bd dolt push` run** (conservative) — sync if desired.
- **Git:** committed `5f52153` on `reel-af-a1-producer-impl`; **NOT pushed**. Two checkouts exist —
  canonical **worktree** `~/ntm_Dev/reel-af-a1-producer-impl/silmari-reels-af` (this branch, committed)
  and the main monorepo checkout `~/ntm_Dev/silmari-agentfield-system/silmari-reels-af`.
- **Test:** `cd <worktree>/silmari-reels-af && uv run --extra dev python -m pytest tests/dsl tests/planner -q` (362 pass; `asyncio_mode=auto`). Key-gated real tests: `test_llm_real.py`, `test_transcribe_real.py`.
- **BAML regen:** edit `baml_src/*.baml` then `.venv/bin/baml-cli generate` (emits/commits `src/baml_client`). Enum values PascalCase; keep field `template_` (no alias).
- **NTM:** You are ORCHESTRATING an ntm session `reel-af-a1-producer-impl` (4 Codex agents, panes 1–4:
  codex-bravo/charlie/delta/echo; user pane 0). Use `ntm --help`. Read panes with
  `ntm --robot-tail=reel-af-a1-producer-impl --lines=N` (parse `panes["1".."4"]`) — NOT tmux. Send to
  agents-only with `ntm --robot-send=reel-af-a1-producer-impl --panes=1,2,3,4 --msg-file=...` (never
  `--all`). **Fresh context per pane = send `/clear` first** (hard `--robot-restart-pane` is NOT
  supported on this window-per-agent layout). Agent Mail identity: **BrownFox**. No other agents
  actively co-working at handoff time.
