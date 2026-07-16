---
date: 2026-07-15
author: Maceo (via Claude orchestration)
status: spec-for-followup-build
repo: silmari-reels-af (reel-af)
base_commit: f72adb4
depends_on: Slice A (2026-07-15-12-44-tdd-reel-af-dsl-hooks-target.md) — the 9a cut-in→CutInOverlay mapper must land first
related_review: 2026-07-15-12-44-tdd-reel-af-dsl-hooks-target-REVIEW.md (BLOCKING-1)
tags: [spec, reel-af, dsl, cut-ins, overlays, zoom, visual, footage-stitch, followup]
---

# Spec: reel-af Cut-In Overlay Rendering (zoom + pop-in) — follow-up build

## Why this is its own build

Slice A (the DSL-hooks render target) was deliberately scoped to **wiring + fail-closed**, not
new rendering. Cut-in **rendering** turned out to be genuine renderer work with a real correctness
hazard, so it is split out here. Slice A ships the **mapper** (9a: A1 `zoom`/`visual` cut-ins →
`overlays.CutInOverlay`, with `CUTIN_INVALID` typed rejection for any cut-in outside every source
segment's span). This build consumes those validated `CutInOverlay` objects and actually **renders**
the effects onto the reel.

Net effect today (Slice A): reels render fully (real-footage stitch + finish captions + hook banner);
zoom/visual cut-ins are mapped + validated but **not yet rendered**. This build closes that gap.

## Goal

Burn A1's line-timed cut-in effects into the produced reel:
- **`zoom`** — a source-segment-relative zoom/pan emphasis over a time window (`at_s`..`until_s`),
  optional `zoom_focus` (e.g. `upper`).
- **`visual`** (pop-in) — a generated image composited over the clip for a window, from the A1
  cut-in's `image_prompt`.

Input contract (from Slice A 9a): a per-segment list of `overlays.CutInOverlay` already validated,
in **source-segment-relative** time, with out-of-segment cut-ins already rejected as `CUTIN_INVALID`.

## The seam — worker level, NOT `footage_stitch.py`

BlueBay's BLOCKING-1 established the real shape of the code:

- `footage_stitch.build_footage_filtergraph` (`footage_stitch.py:151-315`) is a **pure string
  builder** that emits per-segment filter *chains* (`[v0]`,`[v1]`,…) into ONE shared
  `filter_complex` (`:295`), run as a **single** ffmpeg invocation (`_ffmpeg_cmd` `:350-381`,
  `stitch_footage_reel` calls `_run_ffmpeg` once `:338`). **There is no per-segment file boundary
  inside `footage_stitch` to hook.** Do not try to splice overlays into that monolithic graph
  (it would duplicate `overlays.py`, fight `MAX_FILTER_GRAPH_CHARS` `:296-300`, and require input-index
  rewriting).
- The clean boundary is **at the worker in `app.py`**, between the two calls that already exist:
  1. `download_segments` writes one file per segment (`footage_stitch.py:116`: `out_dir/f"{segment_id}.mp4"`).
  2. `stitch_footage_reel(reel, segment_assets, ...)` consumes that `{segment_id: asset}` map.
- **Insert the overlay render between them**: for each segment that has cut-ins, run
  `overlays.render_overlay_clip(segment_path, cut_ins, ...) → overlaid_file`, and substitute the
  overlaid file into `segment_assets`. This uses `overlays.py` exactly as designed — **per-segment
  file in, per-segment file out** — and touches **zero lines of `footage_stitch.py`**.

`overlays.render_overlay_clip` (`overlays.py:200-208`) requires `segment_path.exists()` (`:214-215`);
`build_overlay_filtergraph` builds its own base chain from `[0:v]`
(`overlays.py:145-147`: `scale=…,crop=…,setsar=1,fps=…[base_src]`) and indexes its image inputs from
`visual_input_start=1` (`:116`) — i.e. it assumes it is the **sole graph over a single video input**.
Per-segment-file-in/out satisfies that assumption; splicing into the multi-input stitch graph does not.

## THE core hazard — double normalization (this is the whole risk)

Downloaded segments are **raw, untrimmed source in source coordinates**. `render_overlay_clip`
re-encodes each to a **1080x1920 canvas**. Then `footage_stitch` will `trim`/`scale`/`crop`
**again** (`footage_stitch.py:193-198`) using
`trim_start_s = segment.start_s - asset.source_start_s` (`:191`).

So a naive insertion double-normalizes (scale/crop twice) AND applies a post-overlay trim whose
coordinates no longer match the overlaid frame. This is the single biggest correctness risk and MUST
be designed explicitly, not discovered. Pick ONE resolution and pin it with a test:

- **(A) Overlay AFTER normalization (recommended).** Have the worker first produce the
  **normalized, trimmed** 1080x1920 per-segment clip (the exact frames `footage_stitch` would emit
  for that segment), then run `render_overlay_clip` on THAT, and have `stitch_footage_reel` concat
  the already-finished per-segment clips **without re-trimming/scaling**. Requires either a
  `footage_stitch` "pre-normalized segments, concat-only" mode or extracting its per-segment
  normalize step into a reusable call. Cut-in times then map to trimmed-clip-relative coordinates
  (`at_s - trim_start_s`).
- **(B) Overlay in source coordinates, stitch normalizes once.** Keep overlays in source coordinates
  on the raw segment, and ensure `render_overlay_clip` does **not** scale/crop (disable its base
  chain) so `footage_stitch` remains the sole normalizer. Cut-in times stay source-relative. Risk:
  overlay geometry authored for 1080x1920 must survive the later crop.

Recommendation: **(A)** — it keeps `overlays.py`'s 1080x1920 assumption valid and makes the overlay
frame identical to the final frame. Whichever is chosen, the acceptance test below is mandatory.

## Time base & coordinates

- `CutInOverlay` is **source-segment-relative**; `build_overlay_filtergraph`'s `segment_start_s` is
  satisfiable from `segment.start_s` (`footage_stitch.py:191`). Under resolution (A), convert to
  trimmed-clip-relative (`at_s - trim_start_s`, clamp to `[0, clip_dur]`).
- Keep this subsystem **separate** from `finish_reel`'s `image_cutins`
  (`image_cutins.build_image_overlay_filtergraph`, `finish.py:107-108,264`): that is
  **final-reel-relative**, images-only, LLM-picked, and post-stitch. Do not merge the two models.

## `visual` (pop-in) image sourcing

`visual` cut-ins carry an `image_prompt`; they need a generated/fetched image asset before compositing.
Reuse the existing image provider path used by finish (image generation), but feed it the A1 cut-in's
`image_prompt`. If an image cannot be produced for a mapped `visual` cut-in, fail that cut-in closed
with a typed diagnostic (do not silently drop) — consistent with Slice A's `CUTIN_INVALID` policy.

## Required tests

1. **Per-segment overlay render**: a segment with one `zoom` cut-in yields an overlaid per-segment
   file that `stitch_footage_reel` consumes; output reel duration unchanged.
2. **Zoom present**: the zoom/pan effect is applied over the correct window (probe frames or
   filtergraph assertion).
3. **Visual pop-in composited**: a `visual` cut-in composites the generated image over the correct
   window.
4. **Double-normalization correctness (BLOCKING test)**: prove no double scale/crop and correct
   post-trim timing under the chosen resolution — e.g. a segment with a known trim offset shows the
   cut-in at the right final-reel timestamp and the frame is a single clean 1080x1920 (no letterbox/
   re-crop artifact). This is the test the whole build exists to satisfy.
5. **Fail-closed on missing image asset**: a `visual` cut-in whose image cannot be produced →
   typed diagnostic, no silent drop.
6. **No-cut-in passthrough**: a segment with zero cut-ins is byte-for-byte the current stitch path
   (overlay stage is skipped, not a no-op re-encode).

## Out of scope

- The 9a mapper + `CUTIN_INVALID` validation (ships in Slice A).
- `finish_reel`'s image_cutins path (unchanged).
- Any A1-side change (cut-in authoring lives in `hook-plan.json`, produced by A1).

## Dependencies / order

- **Must follow Slice A** (needs the 9a `CutInOverlay` mapper + validated input).
- Touches: `src/reel_af/app.py` (worker seam), `src/reel_af/render/overlays.py` (consume as designed;
  possibly a "no base-chain" mode for resolution B), and either a `footage_stitch` concat-only mode
  or an extracted normalize step (resolution A). Keep `footage_stitch`'s public behavior back-compatible.
