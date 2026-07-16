---
date: 2026-07-15T13:05:00-04:00
reviewer: BlueBay (Claude Opus 4.8)
plan_under_review: thoughts/searchable/shared/plans/2026-07-15-12-44-tdd-reel-af-dsl-hooks-target.md
plan_author: CopperFinch
repository: silmari-reels-af
base_commit: f72adb4
verification: every code claim re-read against the actual tree at f72adb4; no claim taken on trust
verdict: needs-rework
tags: [review, tdd, plan, dsl, hooks, reel-af, slice-a]
---

# REVIEW — reel-af DSL-Hooks Target TDD Plan (Slice A)

## Verdict: **needs-rework**

This is a strong plan with unusually honest research reconciliation. D1–D6 are
substantially correct, the web-layer claims are near-perfect, the effect-ordering
invariant is preserved, and the CT-1/CT-2 split is an intellectually honest answer
to an out-of-repo control plane. The author clearly read the code rather than the
research.

But **three of the plan's architectural premises do not survive contact with the
code**, and each one invalidates a behavior's Green as specified — not its priority
or its estimate, its *feasibility*:

1. **B2** cannot emit `INVALID_MARKER` from `compile.py` — `read_composite`
   silently swallows every `MarkerError` before the compiler ever sees the marker.
2. **B9** has no per-segment pre-stitch seam to wire cut-ins into — `footage_stitch`
   builds one monolithic `filter_complex` for one ffmpeg exec; there are no
   per-segment files. And the D7 time-base claim that motivates the mapper is
   backwards.
3. **B15** proposes a durable Postgres record as the recovery path for a
   Postgres-outage failure — and this repo contains no migrations at all.

These are not "enhance-then-implement" gaps. B9 and B15 need a design decision
before a test can be written; B2 needs a different file and a data-model change.
Hence **needs-rework**, scoped: the plan's spine (B4/B6/B10–B14/B16) is sound and
could ship largely as written once the three are re-cut.

---

## D1–D7 verification table

| # | Plan's claim | Verdict | Evidence |
|---|---|---|---|
| D1 | `CompileContext` does not exist; sig is `compile.py:50-56`; `SourceRef` at `models.py:174-178` | **VERIFIED** | see D1 below |
| D2 | No `DSL_MARKER_INVALID`; real member `INVALID_MARKER` (`models.py:151`), never emitted; model is `Diagnostic` | **IMPRECISE** | it is a `Literal`, not an enum — see D2 |
| D3 | `_check_unsupported` (`compile.py:132-133`) is a `return False` stub | **VERIFIED but MISFRAMED** | the codes are vestigial, not forgotten — see D3 |
| D4 | `validate_renderable` checks only 4 things; `SourceSegment` doesn't enforce `start_s < end_s` | **VERIFIED** | see D4 |
| D5 | `_resolve_result_ref` wraps `video_path`; `download_url`/`url` unvalidated | **VERIFIED (substance); line refs off by 2; one material omission** | see D5 |
| D6 | Log-only orphan record omitting `target`; `_dispatch_one` unguarded | **VERIFIED (substance)** | see D6 |
| D7 | `overlays.py` dormant; `CutInOverlay` time base is source-segment-relative | **WRONG on the load-bearing half** | see D7 |

### D1 — VERIFIED (exactly as claimed)

`grep -rn "CompileContext" --include="*.py" .` → **0 matches**. Repo-wide, the type
does not exist.

`src/reel_af/dsl/compile.py:50-56`:
```python
def compile_composite(
    doc: CompositeDoc,
    words: WordsSidecar,
    source: SourceRef,
    *,
    relevant_dir: Path | None = None,
) -> CompileResult:
```
`src/reel_af/dsl/models.py:174-178` — `SourceRef` with `source_url: str`,
`source_id: str | None = None`. Exact.

**Backward-compat confirmed, with one correction.** Adding
`context: CompileContext | None = None` as a trailing keyword-only param cannot
break any caller. The sole production importer is
`src/reel_af/render/footage_stitch.py:17-32`, and it imports from
`reel_af.dsl.models` **only** — it never imports `compile_composite` at all. A
repo-wide grep for `reel_af.dsl` outside `src/reel_af/dsl/` returns exactly that
one hit. So `compile_composite` has **zero production callers today**; the D1
compat guarantee is even safer than the plan claims.

**Correction (NICE-TO-HAVE):** the plan says "all 16 `tests/dsl/*` files".
Actual: **18** `test_*.py` files (20 `.py` including `__init__.py` and
`conftest.py`). The number appears in D1 and in B4's success criteria.

### D2 — IMPRECISE (substance right, type kind wrong, and it breaks a Refactor constraint)

Verified: `DSL_MARKER_INVALID` → 0 matches. `DslDiagnostic` → 0 matches.
`Diagnostic` is at `models.py:161-168`. `INVALID_MARKER` is at `models.py:151`
and `grep -rn "INVALID_MARKER" --include="*.py" .` returns **exactly one hit —
the declaration itself**, confirming it is never emitted.

**But it is not an enum.** `src/reel_af/dsl/models.py:143`:
```python
DiagnosticCode = Literal[
```
It is a `Literal[...]` type alias of 14 string literals, not an `Enum`. The plan
calls it "the real enum is `DiagnosticCode`" with "member `INVALID_MARKER`".

This is not pedantry — it invalidates a Refactor constraint. **B2's checkbox**
(plan line 594): *"Named constants: no literal `"INVALID_MARKER"` outside
`DiagnosticCode`."* You cannot reference a member of a `Literal` type; there is no
`DiagnosticCode.INVALID_MARKER` to import. The established codebase idiom is a
bare string at the emission site — `compile.py:143`: `code="UNRESOLVED_HOLE"`.

**Remediation:** drop that checkbox and follow the existing idiom (bare string
literal at the emission site, type-checked against the `Literal` by the
`Diagnostic` model). Converting `DiagnosticCode` to a `StrEnum` would satisfy the
constraint but is a cross-cutting change to 14 codes and every emission site —
out of scope for Slice A. State the choice explicitly so the implementer doesn't
try to honor an impossible constraint.

### D3 — VERIFIED as fact, MISFRAMED as motivation

`src/reel_af/dsl/compile.py:132-133`, verbatim:
```python
def _check_unsupported(doc: CompositeDoc, diagnostics: list[Diagnostic]) -> bool:
    return False
```
Called at `compile.py:62`, before `_check_unresolved`, alignment, and every render
effect — so the fail-closed *ordering* the plan wants is already correct.

**The misframing is load-bearing.** The plan reads the stub as a forgotten
rejection path. The code says otherwise: `[insert relevant]`, `[insert file]`, and
`[find relevant]` are **supported, implemented, tested features** (via
`src/reel_af/dsl/relevant.py`). `UNSUPPORTED_INSERT`/`UNSUPPORTED_FIND` are
**vestigial** — left behind when those features landed. The stub returns `False`
because nothing is unsupported *on the default workflow*. See BLOCKING-4.

### D4 — VERIFIED (exactly as claimed)

`validate_renderable` (`models.py:376-405`) checks exactly four things, duck-typed
via `getattr`: no segments (`:382-384`), transition count vs `segments-1`
(`:390-396`), `duration_s <= 0` (`:398-400`), `duration_s > MAX_REEL_DURATION_S`
(`:402-405`). No finite check, no `start_s < end_s`, no asset resolution.

`SourceSegment` (`models.py:181-189`) confirmed:
```python
start_s: float = Field(ge=0)
end_s: float = Field(gt=0)
```
Independent bounds only — `start_s=5.0, end_s=5.0` constructs cleanly today, and
`float("inf")` passes `gt=0`. B6's Red is genuinely red for the right reason.

**Additive-ness confirmed.** `validate_renderable` is invoked at `compile.py:109-117`
inside a `try/except RenderabilityError` that maps to `NON_RENDERABLE_REEL`, and
`FootageReel._validate_reel` (`models.py:236-284`) is a pydantic
`model_validator` that runs at *construction*, strictly earlier. Strengthening
`validate_renderable` therefore cannot break `FootageReel` construction — it only
adds post-construction rejections. Any reel that today reaches
`validate_renderable` has already satisfied `_validate_reel`. B6 is safe.

### D5 — VERIFIED in substance; line refs off by two; **one material omission**

`_resolve_result_ref` at `web/server.py:224-238`, verbatim:
```python
224	def _resolve_result_ref(execution_id: str, cp_body: dict) -> str | None:
227	    result = cp_body.get("result") or {}
228	    for key in ("download_url", "url"):
229	        value = result.get(key)
230	        if isinstance(value, str) and value:
231	            return value
232	    for key in ("object_uri", "uri", "path"):
233	        value = result.get(key)
234	        if isinstance(value, str) and value.startswith(("http://", "https://", "s3://", "gs://")):
235	            return value
236	    if result.get("video_path"):
237	        return f"cp-execution://{execution_id}/result/video_path"
238	    return None
```
- The `download_url`/`url` verbatim path is **:228-231** (plan says 226-229).
- The scheme gate is **:232-235** (plan says 230-234).
- The asymmetry the plan identifies is real and exactly as described.
- `succeeded` branch at `server.py:697` — VERIFIED.

**Material omission → see BLOCKING-6.** The plan states "A node-local path does
not leak as a browser URL today." True of `result_ref`. **False of the poll
response body.** `_poll_response_body` (`server.py:260-261`) begins
`payload = dict(cp_body)` — the CP body is copied wholesale into the response, so
`result: {"video_path": "/tmp/node/out.mp4"}` **is** returned to the caller today.
B14's own test asserts `"/tmp/node/out.mp4" not in json.dumps(body)`, which will
fail against the plan's Green as written.

**Parity scoping — CONFIRMED sound, but under-covered.**
`ALLOWLISTED_TARGETS` (`reel_jobs.py:58-60`) = `{TARGET_TOPIC, TARGET_COMPOSITE,
TARGET_TEXT_REEL, TARGET_TEXT_CAROUSEL}`. Gating on
`job.target in DELIVERY_REQUIRED_TARGETS` in the `succeeded` branch leaves the
other four untouched — composite/topic/research **do** keep today's fail-soft
behavior. The mechanism is correct. But the parity test
(`test_existing_targets_keep_fail_soft_behavior`) exercises **only
`TARGET_COMPOSITE`** — one of four. See SHOULD-FIX-3.

### D6 — VERIFIED in substance

`web/server.py:339-347`:
```python
339	    try:
340	        deps.reel_jobs.attach_execution_id(ctx, ref.job_id, cp_body["execution_id"])
341	    except HttpError as exc:
342	        deps.logger.error(
343	            "orphaned_dispatch job_id=%s execution_id=%s org_id=%s created_by=%s "
344	            "client_request_id=%s err=%s",
345	            ref.job_id, cp_body["execution_id"], ctx.org_id, ctx.user_id, crid, exc,
346	        )
347	        raise RepositoryUnavailable("dispatch accepted but ownership attach failed") from exc
```
Carries job_id, execution_id, org_id, created_by, client_request_id, err. **`target`
is absent** — confirmed. Two nits: it is a `%s`-formatted string, not structured
key/value fields ("structured" is generous); and `created_by` is `ctx.user_id`.

`_dispatch_one`: **defined at `server.py:424`**; the unguarded attach is the *call*
at **`:443`** (the plan conflates the two). Verified unguarded:
```python
443	    deps.reel_jobs.attach_execution_id(ctx, ref.job_id, cp_body["execution_id"])
444	    return _DispatchOutcome(True, cp_body["execution_id"], "enqueued")
```
**Sharper than the plan states:** every other failure in `_dispatch_one` returns a
`_DispatchOutcome` (`:434-442`). An `HttpError` at `:443` **escapes the function
entirely**, violating its own documented contract (`:425-427`: "Returns a
disposition instead of raising") and aborting the whole fan-out at its call site
`server.py:468`. It is not merely "a silent orphan" — it is a contract breach that
takes down sibling dispatches.

### D7 — **WRONG on the load-bearing half**

Dormancy: VERIFIED. Zero production importers of `overlays`; docstring at
`overlays.py:8-9` names `footage_stitch` as the awaited consumer, exactly as quoted.
`CutInOverlay` at `overlays.py:35-53` — `type`/`at_s`/`until_s`/`line`/
`image_prompt`/`zoom_focus` — supports zoom + visual as claimed.

**But the plan's D7 table says `overlays.CutInOverlay`'s time base is
"source-segment-relative". It is ABSOLUTE SOURCE TIME.** Three independent proofs:

`overlays.py:307-316` — the decisive one:
```python
def _relative_window(
    cut_in: CutInOverlay,
    segment_start_s: float,
    segment_duration_s: float,
) -> tuple[float, float] | None:
    start_s = max(0.0, cut_in.at_s - segment_start_s)
    end_s = min(segment_duration_s, cut_in.until_s - segment_start_s)
    if end_s <= start_s:
        return None
    return start_s, end_s
```
`cut_in.at_s - segment_start_s` — you only subtract the segment origin from an
**absolute** time. `at_s`/`until_s` are absolute source times; *relative* is the
computed output.

`overlays.py:67` — `normalize_cut_ins` docstring: *"Validate and sort cut-ins by
**absolute source time**."*

`overlays.py:121-124` — `build_overlay_filtergraph` docstring: *"`segment_start_s`
is the absolute source timestamp that corresponds to local t=0 in the segment clip.
**Cut-in windows are absolute source times and are clamped to the segment
duration.**"*

**Three consequences, all of which change B9 and R1:**

1. **The mapper the plan specifies is largely redundant.** B9's Green says "Add a
   pure mapper translating hook-plan cut-in dicts (absolute source time) →
   `CutInOverlay` (segment-relative)". A1 cut-ins are absolute source time;
   `CutInOverlay` is absolute source time. The translation is **identity on the
   time fields**. The absolute→relative conversion already exists at
   `overlays.py:307-316` and is applied internally by
   `build_overlay_filtergraph(cut_ins, segment_start_s=..., segment_duration_s=...)`.

2. **R1's central claim is false.** The plan (line 1452): *"A cut-in spanning a
   segment boundary has **no valid representation** in the per-segment model."* It
   has a defined representation: `max(0.0, ...)` / `min(segment_duration_s, ...)`
   **clamps it to each overlapping segment**. A boundary-spanning cut-in renders on
   both segments, clamped to each. That is the library's designed semantics.
   Rejecting it as `CUTIN_INVALID` is a **policy divergence from the library**, not
   a forced consequence of the model — and the plan must justify it as such, or
   drop it. Worse, detecting the spanning case requires re-deriving
   `_relative_window`'s arithmetic in the mapper, violating B9's own checkbox
   ("No duplication: reuse `overlays.normalize_cut_ins`").

3. **The genuinely missing rejection is a different one.** A cut-in outside
   *every* segment is **silently dropped** today — filtered out at
   `overlays.py:137-141` via `_relative_window(...) is not None`. *That* is the real
   `CUTIN_INVALID` case worth closing, and it's the one B9's second Red test
   (`test_unmappable_cutin_is_typed_rejected_not_dropped`, `CUTIN_PAST_END_OF_REEL`)
   actually exercises. Keep that one; drop the boundary-spanning rejection.

**Assessment of R1 as written: the risk is misdiagnosed, and it understates the
real one.** The mapping is *well-defined* — better-defined than the plan believes.
The actual blocker in B9 is that the consumer seam does not exist (BLOCKING-1).

---

## BLOCKING findings

### BLOCKING-1 — B9 wires cut-ins into a "per-segment / pre-stitch seam" that does not exist

**Where:** plan B9 Green (line 966-969), "Files touched:
`src/reel_af/render/footage_stitch.py`"; D7 table row "stage: per-segment, pre-stitch".

**The code:** `footage_stitch.py` has **no per-segment render step and no
per-segment files**.

- `build_footage_filtergraph` (`footage_stitch.py:151-315`) is a **pure string
  builder** — its docstring (`:152`): *"Build the ffmpeg filtergraph without
  filesystem or subprocess effects."* It emits per-segment filter *chains* into a
  single shared `filters` list (`:193-198`, labels `[v0]`, `[v1]`, …), joined at
  `:295` into **one** `filter_complex` string.
- `_ffmpeg_cmd` (`:350-381`) turns that into **one** ffmpeg invocation: `-i` per
  input (`:352-353`), a single `-filter_complex`, one `-map` video / one `-map`
  audio (`:354-360`), one output (`:379`).
- `stitch_footage_reel` (`:318-347`) calls `_run_ffmpeg` exactly once (`:338`).

So segment normalization is per-segment *in intent* but happens as chains inside a
monolithic graph over all inputs — never as discrete files. There is no boundary to
hook.

**Why `overlays.py` cannot splice into that graph:**
- `render_overlay_clip` (`overlays.py:200-208`) takes `segment_path: Path` and
  checks `.exists()` (`:214-215`) — it requires **a file per segment**.
- `build_overlay_filtergraph` builds **its own base chain** from `[0:v]`
  (`overlays.py:145-147`: `[0:v]scale=...,crop=...,setsar=1,fps=...[base_src]`) and
  indexes its own image inputs from `visual_input_start: int = 1` (`:116`). It
  assumes it is the **sole graph over a single video input**. Splicing it into
  `footage_stitch`'s multi-input graph means rewriting every input index and
  discarding its base chain — i.e. reimplementing it.
- `footage_stitch` already caps graph length at `:296-300`
  (`MAX_FILTER_GRAPH_CHARS`); injected overlay chains push directly against it.

**What *is* available:** `segment.start_s` (absolute source time) is in scope at
`footage_stitch.py:191` (`trim_start_s = max(0.0, _float_attr(segment, "start_s") - ...)`),
so `build_overlay_filtergraph`'s `segment_start_s` requirement is satisfiable.
**Data availability is not the blocker — the file boundary is.**

**Remediation (choose one, and say which in the plan):**

- **(a) — recommended.** Put the seam at the **worker level in `app.py`**, between
  the two existing calls, not inside `footage_stitch.py`. `download_segments`
  already writes one file per segment (`footage_stitch.py:116`:
  `out_dir / f"{segment_id}.mp4"`) and `stitch_footage_reel(reel, segment_assets, ...)`
  consumes that map. Insert `overlays.render_overlay_clip` between them, producing
  overlaid per-segment files, and pass those as `segment_assets`. This uses
  `overlays.py` exactly as designed (per-segment file in, per-segment file out) and
  touches **zero** lines of `footage_stitch.py`.
  **Caveat that must be designed, not discovered:** downloaded segments are raw,
  untrimmed source in source coordinates, and `render_overlay_clip` re-encodes to a
  1080x1920 canvas. `footage_stitch` will then `trim`/`scale`/`crop` **again**
  (`:193-198`), using `trim_start_s = segment.start_s - asset.source_start_s`
  (`:191`). Double normalization plus post-overlay trim coordinates is a real
  correctness hazard — this is the behavior's true risk and needs its own test.
- **(b)** Splice overlay chains into `build_footage_filtergraph` with input-index
  remapping — duplicates `overlays.py`, contradicts B9's no-duplication checkbox,
  fights `MAX_FILTER_GRAPH_CHARS`. Not recommended.

**Either way B9 is not "wiring".** It adds a render stage. The plan's framing
("This is a wiring + fail-closed slice, not a renderer slice", line 23) does not
hold for B9. **Split B9** into (9a) the pure cut-in→`CutInOverlay` mapper +
`CUTIN_INVALID` for out-of-every-segment (cheap, pure, testable now) and (9b) the
per-segment overlay render stage (the real work, with the double-normalization
test). 9b is the single biggest implementation risk in the slice.

### BLOCKING-2 — B2 cannot emit `INVALID_MARKER` from `compile.py`; `read_composite` has already swallowed the error

**Where:** plan B2 Green (line 587-589): *"Surface `MarkerError` from the read/parse
path into a typed `INVALID_MARKER` diagnostic in `compile_composite`"*; "Files
touched: `src/reel_af/dsl/compile.py`".

**The code:** `read_composite` swallows `MarkerError` in **both** marker paths, and
drops the marker on the floor.

`src/reel_af/dsl/composite.py:99-101` (inline marker):
```python
                except MarkerError:
                    pass
```
`src/reel_af/dsl/composite.py:121-125` (standalone marker line):
```python
            try:
                parsed = parse_marker(marker_text, source=src)
            except MarkerError:
                trailing_ok = False
                continue
```
In both cases the malformed marker is **never appended to `markers`**. The
`CompositeDoc` handed to `compile_composite` carries **no record that it ever
existed**. `compile_composite` cannot diagnose what it cannot see. No change
confined to `compile.py` can turn B2's Red green.

**Trace of B2's own Red test:** `text = "[bogus 1.0]"` → standalone marker line →
`parse_marker` raises `MarkerError("unknown verb: 'bogus'")` (`parser.py:74`) →
swallowed at `composite.py:123` → doc has 1 segment, **0 markers** → compile runs
clean → `status == "ok"`, `plan is not None`. The test is red, but red for *"no
diagnostic exists at all"*, and the plan's Green cannot fix it.

**Also: the three parametrized cases have three different mechanisms, and one is
undiagnosable in principle.**
- `"[bogus 1.0]"` → `MarkerError` (unknown verb, `parser.py:74`) → swallowed. ✅ fixable.
- `"[insert"` → **no closing bracket** → `_MARKER_LINE_RE` does not match → it is
  never treated as a marker at all, just ignored non-timecode text. **No
  implementation can emit `INVALID_MARKER` for this** without a heuristic
  "looks-like-a-broken-marker" scanner. Remove it from the parametrize list or
  scope it explicitly.
- `"[trans notaprimitive 1.0]"` → may raise `MarkerError` in `_parse_trans` (→
  swallowed) **or** parse cleanly and fail later as `INVALID_TRANSITION`
  (`models.py:153`, a *different* code). Asserting `INVALID_MARKER` for it is a coin
  flip on parser internals.

**Remediation:**
1. Retarget B2: **"Files touched" must be `src/reel_af/dsl/composite.py` +
   `src/reel_af/dsl/models.py` (the `CompositeDoc` model)**, not `compile.py`.
2. Add a `CompositeDoc.invalid_markers: list[...]` field (or have `read_composite`
   accept a diagnostics sink) so the swallowed `MarkerError` survives into the doc;
   `compile_composite` then emits `INVALID_MARKER` from it, alongside
   `_check_unsupported`/`_check_unresolved` at `compile.py:62-68`.
3. Narrow the parametrize to cases that are actually `MarkerError`s.
4. **Flag the latent bug this exposes** — worth stating in the plan as the
   motivation: today a typo'd `[trans fade 1.0` (or `[bogus]`) is *silently ignored*
   and the reel renders without the transition, no diagnostic, no warning. That is a
   silent-failure defect in the current parser, and B2 is the right place to close
   it. This makes B2 more valuable than the plan claims, not less.

### BLOCKING-3 — B15's durable Postgres record is unreachable in its own failure mode, and this repo has no migrations

Two independent problems; either alone blocks.

**(a) The outage paradox.** The orphan fires when `attach_execution_id` raises,
and the handler converts it to `RepositoryUnavailable` (`web/deps.py:90-91`:
`status, code = 503, "service_unavailable"`). The trigger **is** database
unavailability. The plan's remedy is to write a row to
`deepresearch.orphaned_dispatch` — **in the same Postgres that just refused the
write**. In production that record write fails too, and the orphan is lost exactly
when it matters.

B15's Red test hides this behind the fake:
```python
repo = FakeReelJobRepo(attach_error=RepositoryUnavailable("db down"))
...
(rec,) = repo.orphaned                      # durable, not just a log line
```
The same fake repo that just raised *"db down"* is asked to durably persist. **The
fake will pass; production cannot.** This is a false-green at the design level, not
the test level — the most dangerous kind, because the test suite will certify it.

**(b) There are no migrations in this repository.** Verified: no `migrations/`
directory, no alembic, no DDL anywhere. The only `.sql` file in the tree is
`thoughts/searchable/shared/runbooks/research_run_prod_audit.sql` (a runbook).
`web/pg.py:1-12` states the ownership model outright:

> *"reels-af consumes the root-owned user-data schema; it never owns or vendors the
> migrations (`migrations/deepresearch/` at the monorepo root, applied against the
> Railway `user_data` DB)."*

That path is **in a different repo**. And the gate is worse than a release-ordering
nuisance: a new table must be declared in `FEATURE_SCHEMA` (`pg.py:49-68`), which
`_assert_schema` (`pg.py:92-107`) verifies against `information_schema.columns`,
raising `SchemaUnavailable` → **503 across the entire feature surface** until the
root migration lands. `.github/workflows/ci.yml` has one job (ffmpeg, ruff, pytest)
— **no migration gate whatsoever**. So the plan's manual criterion *"Migration
reviewed"* (line 1332) understates this by an order of magnitude: it is a
cross-repo release-ordering dependency that can 503 the whole app, verified by
nothing in CI.

**Remediation — Slice A can and should avoid the new table.** Answering the
scoping question directly: **yes, there is an existing durable surface, and it is
already the right shape.**

The orphan's precondition is that **the job row already exists** —
`insert_or_get_queued` succeeded (`server.py:323`) *before* dispatch (`:329`) and
attach (`:340`). The orphan state is precisely: `status='queued' AND execution_id
IS NULL`. And `mark_stale_queued` (`pg.py:372-387`) **already sweeps exactly that
predicate**:
```sql
update deepresearch.reel_job set status = 'failed', completed_at = %s
where status = 'queued' and execution_id is null
and created_at < %s - make_interval(secs => %s)
```
(`REEL_DISPATCH_STALE_S`, default 900.) The durable, queryable, org-scoped record
of an orphaned dispatch is **the `reel_job` row itself**, and a reaper already
exists. Recommended re-cut of B15 for Slice A:

1. **Keep** the existing log (`server.py:342-346`) and **add `target`** to it —
   a one-line, zero-migration fix that closes the plan's stated gap.
2. **Wrap `_dispatch_one`'s attach** (`server.py:443`) in the same handler so the
   fan-out path stops breaching its `_DispatchOutcome` contract (D6 gap 2). This is
   the genuinely valuable half of B15 and needs no schema at all.
3. **Defer the durable record + repair path** to a slice that can coordinate the
   root migration — or, better, replace it with a **CP-reconciling sweep** that
   re-queries the control plane for executions belonging to stale-queued rows and
   attaches them. That is a real repair (it recovers the job), it reuses
   `mark_stale_queued`'s predicate, and it needs no new table.
4. If the durable table survives review anyway: the plan **must** state the
   cross-repo migration as an explicit release gate with the `FEATURE_SCHEMA`
   503-blast-radius spelled out, and must resolve the outage paradox (e.g. scope the
   record to non-`RepositoryUnavailable` attach failures only).

### BLOCKING-4 — B3's Green contradicts three currently-green tests, and its signature omits `context`

**The tests it breaks.** `tests/dsl/test_compile_unsupported.py` — docstring
(`:1`): *"B12 → Tier-4: insert relevant / insert file / find relevant compile
correctly."* It asserts, on green today:
- `:55` — `[insert relevant 5]` → `assert "UNSUPPORTED_INSERT" not in codes`
- `:85` — `[insert file rel_01]` → `assert "UNSUPPORTED_INSERT" not in codes`
- `:123` — `[find relevant 30 x5]` → `assert "UNSUPPORTED_FIND" not in codes`

These markers are **working features**. `[insert file rel_01]` compiles to source
segments via `relevant_dir` (`:77-83`); the test file exists to pin that.

**The plan's Green rejects them unconditionally.** Plan line 634:
```python
def _check_unsupported(doc: CompositeDoc, diagnostics: list[Diagnostic]) -> bool:
```
No `context` parameter. And the call site is unchanged at `compile.py:62`:
`unsupported = _check_unsupported(doc, diagnostics)`. As specified, any
`_unsupported_code_for(att.marker)` that returns a code for `insert`/`find` fires
for **every** caller — breaking those three tests, contradicting **B3's own success
criterion** ("Green; `_check_unresolved` behavior unchanged (regression)") and
**B4's** ("All 16 `tests/dsl/*` files still green with **no edits**").

**Remediation:**
1. Thread the context: `_check_unsupported(doc, diagnostics, context=context)`;
   **`context is None` → `return False`** (byte-for-byte today's behavior, which is
   what makes the three tests stay green and what D1's compat guarantee actually
   requires here).
2. Gate rejection on the DSL-hooks workflow only. The "unsupported set" is
   **per-workflow**, not global — the plan's checkbox "the unsupported-marker set is
   a module constant" should be a constant *keyed by workflow*, e.g.
   `DSL_HOOKS_UNSUPPORTED_VERBS`.
3. **State B3's hidden dependency on B4 explicitly.** B3's Red already passes
   `context=DSL_HOOKS_CONTEXT` (line 623), so it cannot even be written before B4
   lands. Implementation Order does happen to sequence B4 → B3 correctly, but the
   dependency is never named — an implementer taking B3 standalone will be stuck.
4. Reframe D3: the codes are **vestigial**, not forgotten. What Slice A adds is a
   *workflow-scoped* rejection policy, which is a new concept, not a stub fill-in.

### BLOCKING-5 — B17 carries the entire closure guarantee but is neither BLOCKING nor execution-guaranteed

The CT-1/CT-2 split is defensible — the CP genuinely is out-of-repo (`research
lines 1460-1463`), and splitting beats mocking the span. But the plan is explicit
that **B17 is what makes the split honest**:

> **R5** (line 1476-1479): *"The CP is out-of-repo, so no single test spans
> submit→worker→poll. The golden-fixture parity test (B17) is what keeps the two
> spans honest. If that fixture is ever hand-edited instead of regenerated, the
> closure guarantee silently degrades."*

The plan diagnoses the hazard and then **does not gate it**:
- **B17 is not classified BLOCKING.** CT-1 and CT-2 are. The test that joins them
  is not. Per the closure framework, the load-bearing verification inherits the
  classification of what it guarantees.
- **B17 has no execution guarantee.** Its success criteria (line 1442-1444) never
  mention ffmpeg or the fail-closed `_require_ffmpeg()` pattern. If
  `REAL_WORKER_RESULT` comes from actually running the worker, it needs ffmpeg and
  must fail closed; the plan is silent.
- **`REAL_WORKER_RESULT` is undefined.** Plan line 1430:
  `assert json.loads(read_fixture("dsl_hooks_execution_result.snapshot.json")) == REAL_WORKER_RESULT`.
  If it's a literal in the test file, the test is a **tautology comparing two
  hand-authored constants** — precisely the degradation R5 warns about, shipped on
  day one. If it's produced by running the worker, B17 *is* a second CT-1 and needs
  CT-1's execution guarantee.
- **The cited precedent may not hold.** The plan says to generate the fixture "as
  `tests/web/fixtures/execution_result.snapshot.json` already does for composite"
  (line 1434-1435). That fixture exists, but it is only ever *read* — by
  `tests/web/test_research_event_contract.py:58,79,88,94,101,108`. No generator
  script was found. If the precedent is a hand-authored fixture, it is precedent for
  the wrong thing.

**Remediation:** classify **B17 as BLOCKING**; specify `REAL_WORKER_RESULT` as the
literal return value of an in-test `dsl_hooks_to_reels(...)` invocation (not a
constant); add `_require_ffmpeg()` fail-closed; and add a regeneration command to
the plan so "regenerated, not hand-edited" is a mechanical step rather than a
discipline. Without this, CT-1 and CT-2 can both be green while the payload contract
between them has silently diverged — the exact failure the split was designed to
prevent.

### BLOCKING-6 — B14's Green cannot satisfy B14's own tests (poll body leaks `video_path`; `error` is unsettable)

Two concrete gaps between B14's tests and B14's Green (line 1242-1253), both in
`_poll_response_body` — a function the plan never mentions.

**(a) The node-local path leaks through `dict(cp_body)`.** `web/server.py:260-270`:
```python
260	def _poll_response_body(cp_body: dict, normalized: ReelJobStatus, job=None) -> dict:
261	    payload = dict(cp_body)
262	    payload["status"] = normalized
...
266	    if normalized == "failed" and "error" not in payload:
267	        error = _execution_error(cp_body)
268	        if error is not None:
269	            payload["error"] = error
270	    return payload
```
Line 261 copies the **entire CP body** into the response, including
`result: {"video_path": "/tmp/node/out.mp4"}`. B14's test asserts:
```python
assert "/tmp/node/out.mp4" not in json.dumps(body)     # never exposed
```
This **fails** against the plan's Green, which only changes the reconcile branch at
`server.py:697`. Suppressing `result_ref` does not suppress the raw CP `result`
dict. To make the test pass, `_poll_response_body` must strip or redact `result` on
the delivery-unavailable path — unmentioned in the plan. (This also refines D5: the
plan's "a node-local path does not leak as a browser URL today" is true of
`result_ref` and **false** of the poll body.)

**(b) `error` cannot be set from a locally-derived condition.** `:266-269` populates
`payload["error"]` **only** from `_execution_error(cp_body)` — i.e. an error the
**CP** reported. In the `delivery_unavailable` case the CP reported `succeeded`;
there is no error in `cp_body`. So B14's `assert body["error"] == A1_DELIVERY_UNAVAILABLE`
cannot pass without extending `_poll_response_body` to accept a locally-derived
error. Also unmentioned.

**Remediation:** add `web/server.py:260-270` to B14's "Files touched"; specify the
`result`-stripping rule and the locally-derived-error parameter; add a test that the
whole `result` dict — not just `result_ref` — is absent from the response on the
delivery-unavailable path.

---

## SHOULD-FIX findings

### SHOULD-FIX-1 — B4's backward-compat test is a tautology and cannot be red for the right reason

Plan line 692-694:
```python
def test_context_defaults_none_preserves_today_behavior():
    assert compile_composite(DOC, WORDS, SOURCE) == compile_composite(DOC, WORDS, SOURCE, context=None)
```
Both sides take **the same code path by construction** — the default *is* `None`.
The assertion passes the instant the parameter exists and proves nothing about
"byte-for-byte today's behavior" (line 675). And pre-change it is red for
`TypeError: unexpected keyword argument 'context'`, not for any behavioral
difference — a red for the wrong reason.

The D1 guarantee is genuinely carried by B4's *other* success criterion — "all
`tests/dsl/*` still green with no edits" — which is the correct proof and is
already listed. **Remediation:** delete this test, or replace it with a
characterization assertion against a snapshot of today's `CompileResult` captured
before the change.

### SHOULD-FIX-2 — `fetch_segment` is **not** a pre-existing production injection point; CT-1's "no span bypass" claim is overstated

CT-1 (line 462-465): *"Only two things are injected: `SegmentFetchFn` ... and the
`uploader` seam (`FakeS3` via `client_factory`) — both are **pre-existing
production injection points** (`footage_stitch.py:85`, `app.py:1315-1316`), not span
bypasses."*

- **`uploader` — VERIFIED.** `app.py:1315-1316`:
  ```python
  1315	    if uploader is None:
  1316	        from reel_af.storage import upload_reel as uploader
  ```
  A real pre-existing seam with a production default. (`research_to_reel` actually
  has **seven** keyword seams at `:1210-1217`, not the four its own stale docstring
  at `:1228-1230` claims.)
- **`fetch_segment` — does not exist.** It is absent from `research_to_reel`'s
  signature and from `app.py` entirely; `app.py` has **zero** `dsl`/`footage_stitch`
  coupling (`grep -n "dsl" src/reel_af/app.py` → no matches). `SegmentFetchFn`
  (`footage_stitch.py:85`) is a type alias and `download_segments`' third positional
  param — a real seam **in `footage_stitch`**, but not at the reasoner boundary CT-1
  triggers from.

The seam is still **legitimate** (a production parameter with a production default,
mirroring `uploader`) — but it is **created by this slice**, not inherited. The
claim as written overstates the closure argument's safety.
**Remediation:** reword to "`uploader` is pre-existing (`app.py:1315-1316`);
`fetch_segment` is a new production seam this slice adds on
`dsl_hooks_to_reels`, defaulting to the production fetcher, mirroring the
`uploader` pattern."

### SHOULD-FIX-3 — B14's parity test covers 1 of 4 non-DSL-hooks targets

`ALLOWLISTED_TARGETS` (`reel_jobs.py:58-60`) has four members; D5's promise is that
**all** of them keep today's fail-soft behavior. The parity test
(`test_existing_targets_keep_fail_soft_behavior`, line 1234-1238) exercises only
`TARGET_COMPOSITE`. **Remediation:** parametrize over
`sorted(ALLOWLISTED_TARGETS - DELIVERY_REQUIRED_TARGETS)` so the guarantee is pinned
by construction and any future allowlisted target inherits the parity check
automatically.

### SHOULD-FIX-4 — B14 introduces a second, weaker URL-validation idiom, violating B12's own checkbox

Plan line 1247-1248:
```python
def _is_browser_deliverable(ref: str | None) -> bool:      # pure question
    return isinstance(ref, str) and ref.startswith(_BROWSER_DELIVERABLE_SCHEMES)
```
`_is_valid_url` (`reel_jobs.py:151-156`) already exists: `urlparse`, scheme in
`("http","https")`, **and non-empty netloc**. The new predicate accepts the string
`"https://"` (scheme, no host) — strictly weaker. And **B12's own Refactor
checkbox** (line 1135) says: *"No duplication: reuse `_is_valid_url`; do not
re-implement URL parsing."* B14 re-implements it.
**Remediation:** `_is_browser_deliverable` should delegate to `_is_valid_url`.

### SHOULD-FIX-5 — `delivery_unavailable`: status vs error code is left ambiguous, and one reading is infeasible

The plan uses "terminal `delivery_unavailable`" as though it were a **status**
(Desired End State #3 line 303-305; B14's title; CT-1's OBSERVE line 458), while
B14's test asserts a **status of `failed` plus an error code**
(line 1225-1226). These are different contracts.

The code settles it: `update_from_execution` (`pg.py:358-370`) writes
`status = %s` typed `ReelJobStatus`, guarded by
`and status not in ('succeeded','failed','cancelled')` — the terminal set is
**hardcoded in SQL**. A new `delivery_unavailable` *status* would need the
`ReelJobStatus` literal, that SQL predicate, and a root-owned schema change — i.e.
BLOCKING-3's problem again. The plan's own checkbox ("Do not reorder DB status
strings (externally-owned)") implies it already intends `failed` + error code.
**Remediation:** state once, near Desired End State: *"status remains `failed`;
`delivery_unavailable` is the error **code** in the poll response body"* — and fix
the three places that read as a status.

### SHOULD-FIX-6 — D5/D6 line references are off by two; `_dispatch_one`'s def/call conflated

Not substantive, but the plan's value is its `file:line` precision, and an
implementer will jump to these:
- `download_url`/`url` verbatim: **`server.py:228-231`** (plan: 226-229).
- `object_uri`/`uri`/`path` scheme gate: **`server.py:232-235`** (plan: 230-234).
- `_dispatch_one`: **defined `:424`**, unguarded attach **called `:443`** (plan
  reads as though `:443` is the def).
- D6's "structured" log is a `%s`-format string (`server.py:342-346`), not
  structured fields.

---

## NICE-TO-HAVE findings

- **The plan file has leaked tool-call XML.** Lines 1521-1523 contain `</content>`
  and `</invoke>`. Strip them.
- **"16 `tests/dsl/*` files"** — actual is **18** `test_*.py` (20 `.py` including
  `__init__.py`/`conftest.py`). Appears in D1 and B4's success criteria.
- **`AgentRouter` args incomplete.** `app.py:98` is
  `AgentRouter(prefix="reel", tags=["video", "viral"])`; the plan omits `tags`. The
  target-id derivation conclusion is unaffected and **correct** — `dsl_hooks_to_reels`
  is the right function name for `reel-af.reel_dsl_hooks_to_reels`.
- **`app.py:848`/`:1323` are not spreads.** They are assignments
  (`delivered = {"download_url": download_url} if download_url else {}`); `:501`/`:661`
  are the actual conditional spreads (`**({...} if download_url else {})`). The plan's
  "All four conditionally spread it" is imprecise; the anti-pattern conclusion at
  `:1336` (`"reel_ref": download_url or final.get("video_path", "")`) is **verified
  exact**.
- **B1 and B7 are characterization tests, not TDD cycles.** B1's Red is red because
  a fixture file is missing; B7's Green says "Characterization". The plan is honest
  about both ("If it fails, the failure is the finding") — no action needed, but
  they shouldn't be counted as behavioral reds when assessing coverage.
- **`_CP_STRIP` (`reel_jobs.py:162-166`) appears dead** — no usages found. It also
  duplicates `_METADATA_INPUT_KEYS`' contents as a literal instead of reusing the
  constant. Pre-existing; worth a follow-up bead, not this slice.

---

## Review dimensions requested

### Scope discipline — ✅ clean

No A1-side leakage found. Ingest, the `hook-plan.json` *producer*, `meta.json` v2,
the `composite.ts.md` producer, `POST /api/video-runs`, `dispatch-manifest.json`,
and `run.lock` are all explicitly excluded (lines 319-322) and appear nowhere in
any behavior. A1 artifacts are consumed strictly as fixtures
(`tests/dsl/fixtures/a1_*`). "What We're NOT Doing" is unusually disciplined — each
deferral carries a rationale and a tracked risk. This dimension is a model for
other slices.

### Fail-closed coverage (B13) — ✅ ordering correct

Verified against `_handle_submit` (`server.py:310-350`). `build_submission` is
called at `:315`; `insert_or_get_queued` at `:323`; `dispatch_async` at `:329`.
Every rejection B13 specifies raises `BadRequest` inside `build_submission`
(`reel_jobs.py:235-353`) — **structurally before** any row or CP call. The
assertion pair (`repo.inserted == []`, `cp.dispatch_calls == []`) is the correct
proof and matches the established pattern at `tests/web/test_submit.py:25-52`.

Coverage of the named shapes is complete: `TARGET_ARTICLE` (`reel_jobs.py:26`) is
**confirmed absent** from `ALLOWLISTED_TARGETS` (`:58-60`) → 400 via `:253`;
topic/clip-plan/upload-handle/local-path all rejected by
`_reject_unsupported_fields` against `DSL_HOOKS_ALLOWED_INPUT_KEYS`; the
`?t=&reel_end=` article-seed guard is an explicit addition. One note: the
`_METADATA_INPUT_KEYS` union pattern (`reel_jobs.py:71-73`, applied at `:77-87`) is
correctly mirrored — `{"client_request_id", "research_run_id",
"source_research_run_id"}` will be permitted under `input`, which B11's test
(`assert "client_request_id" not in body["input"]`) correctly pins as
*stripped from the CP body* rather than rejected at the door. That distinction is
right and worth keeping.

### Effect-ordering invariant — ✅ preserved; no side effects in control expressions

The claimed chain matches `server.py:310-350` exactly: `identity.resolve` (`:311`)
→ `access_guard.authorize_create` (`:312`) → `build_submission` incl. forbidden
identity (`:315-317`, rejecting at `reel_jobs.py:251` top-level and `:259` under
`input`) → `_resolve_cp_input` (`:318`) → `insert_or_get_queued` (`:323`) →
`dispatch_async` (`:329`) → `attach_execution_id` (`:340`) → poll (`:683+`) →
`update_from_execution` (`:699`).

The plan's additions are all append-only and preserve it. `build_submission`'s
branch chain (`:249/:253/:257/:261/:277/:296`) is guard-clause style — every branch
returns — so appending an `if target == TARGET_DSL_HOOKS:` branch before the
unreachable guard (`:351-353`) cannot perturb existing targets. The instruction to
"not reorder" is correctly stated.

No side effects inside control expressions were found in any proposed Green.
`_is_browser_deliverable` and `_unsupported_code_for` are both specified as pure
predicates, and B15's checkbox explicitly forbids writes inside conditionals. One
observation: **the plan's Greens are cleaner than the code they're joining** —
`_check_unresolved` (`compile.py:136-149`) mutates its `diagnostics` argument
inside a nested loop, and B3's Green correctly mirrors that shape rather than
inventing a new style. Right call.

### Named constants + CodeCleanup — ✅ present, with two dead constraints

Constants are consistently specified (`TARGET_DSL_HOOKS`, `DSL_HOOKS_SOURCE_MODE`,
`A1_DELIVERY_UNAVAILABLE`, `DELIVERY_REQUIRED_TARGETS`,
`_BROWSER_DELIVERABLE_SCHEMES`, `A1_MIN_HOOK_CLIP_S`/`A1_MAX_HOOK_CLIP_S`, variance
floors), and R2's instruction to reuse `models.py:28-29` rather than add a fourth
`CANVAS_WIDTH` declaration is correct and verified (`models.py:28-29` and
`overlays.py:22-23` are indeed both `1080`/`1920`).

Two constraints cannot be honored as written: **B2's** "no literal
`"INVALID_MARKER"` outside `DiagnosticCode`" (D2 — it's a `Literal`, not an enum)
and **B14's** duplication of `_is_valid_url` against B12's own no-duplication
checkbox (SHOULD-FIX-4).

### Closure tests — classification right, execution honest, one fatal gap

- **CT-1 / CT-2 both correctly BLOCKING.** Both cross new registration points and
  module boundaries; neither is a leaf.
- **OBSERVABLE via production read paths — ✅.** CT-2 observes `_handle_poll`'s
  response body plus the row reconciled through `update_from_execution`
  (`pg.py:358-370`), explicitly "never a raw store read". CT-1 observes the
  reasoner's returned dict. Correct.
- **Execution guarantee — ✅ and verified.** CT-1's fail-closed claim checks out:
  `.github/workflows/ci.yml:16-17` does `apt-get install -y ... ffmpeg`, and the
  fail-closed pattern exists at `tests/test_finish_closure.py:37-39`:
  ```python
  def _require_ffmpeg() -> None:
      if not FFMPEG or not FFPROBE:
          pytest.fail("B9 closure requires ffmpeg + ffprobe on PATH (fail-closed)")
  ```
  distinct from the ordinary `requires_ffmpeg` skipif (`tests/util.py:26-29`). The
  plan picks the right one for BLOCKING gates and says so. **CT-2 is hermetic** —
  pure fakes via `make_deps`/`FakeControlPlane`/`FakeReelJobRepo`, no infra; only
  the `integration` marker (`pyproject.toml:56-58`, the sole marker) needs
  `TEST_DATABASE_URL`. So: **CT-1 needs real ffmpeg and fails closed without it;
  CT-2 needs nothing.** The plan states this accurately. The worker's yt-dlp/Remotion
  exposure is correctly avoided — `fetch_segment` returns a local `lavfi` mp4 and
  Remotion isn't on this path at all.
- **RED-AT-SEAM — mostly valid, one invalid.** CT-2's proofs are sound (removing
  `TARGET_DSL_HOOKS` from `ALLOWLISTED_TARGETS` → 400 at `reel_jobs.py:253`;
  disabling the delivery policy → wrongly-`succeeded`). CT-1's `upload_reel → None`
  proof is sound (`storage.py:43-73` returns `None` when unconfigured, so it's a
  reachable production state). **CT-1's cut-in proof is not** — "disable the
  cut-in→`CutInOverlay` wiring → the test goes red on 'cut-ins not burned in'"
  presupposes the seam in BLOCKING-1. Until B9 is re-cut, that proof cannot be
  executed.
- **DRIVABILITY — ✅.** Store seam (`uploader`, `client_factory`) and fetch seam
  present; CT-1's span is synchronous, so "no clock needed" is correct, not a gap.
  CT-2's `FixedClock` exists.
- **The fatal gap is BLOCKING-5** — the parity test that joins the two spans is
  ungated.

### TDD soundness across 17 behaviors

**Genuinely red for the right reason (verified against f72adb4):** B6 (`start_s ==
end_s` and `float("inf")` provably pass `SourceSegment` today), B10–B13
(`TARGET_DSL_HOOKS` is not in `ALLOWLISTED_TARGETS`, so submit 400s at
`reel_jobs.py:253`), B14 (`video_path`-only provably reconciles `succeeded` via
`server.py:697` + `:236-237`; non-`http(s)` `download_url` provably passes verbatim
via `:228-231`), B15 (`_dispatch_one:443` provably unguarded), B16 (`app.py` has
zero dsl imports), B5 (no worker exists).

**False-greens / bad reds identified:**
1. **B4's `test_context_defaults_none_preserves_today_behavior`** — tautology
   (SHOULD-FIX-1).
2. **B15's `test_attach_failure_writes_durable_orphan_record`** — passes on a fake
   that cannot exist in production (BLOCKING-3a). The most dangerous one: it
   certifies an unreachable recovery path.
3. **B17's `test_ct2_golden_fixture_matches_real_worker_output`** — tautology if
   `REAL_WORKER_RESULT` is a literal (BLOCKING-5).
4. **B2's `[insert` case** — cannot be made green by any implementation
   (BLOCKING-2).
5. **B14's two assertions** that its own Green cannot satisfy (BLOCKING-6) — these
   are *good* tests; the Green is what's incomplete.
6. **B1/B7** are characterizations, not reds — honestly labeled.

**Note on B5 vs CT-1's FORBIDDEN SPAN:** B5 monkeypatches `stitch_footage_reel` and
`finish_reel`, both in CT-1's forbidden span, and both live in
`tests/test_dsl_hooks_worker_closure.py` (CT-1's file). This is **acceptable** —
B5's assertion is `calls == []`, a *negative* assertion that requires patching to
observe non-invocation, and it's a different test from CT-1's. Worth a comment in
the file so a future reader doesn't "fix" the apparent violation.

### Biggest implementation risk

**B9 (BLOCKING-1), and the plan's own R1 points at the wrong thing.** R1 worries the
cut-in time-base mapping is ill-defined; it is in fact *well*-defined
(`overlays.py:307-316` clamps, deterministically). The real risk is that **the
consumer seam does not exist**, and creating it means adding a per-segment render
stage whose interaction with `footage_stitch`'s existing trim/scale/crop
(`:191-198`) is a genuine double-normalization correctness hazard. That is renderer
work, not wiring — it contradicts the slice's own framing (line 23) and is the one
behavior most likely to blow the estimate.

**Behaviors that should be split:** B9 → **9a** (pure mapper +
`CUTIN_INVALID` for cut-ins outside every segment — cheap, pure, no seam needed) and
**9b** (per-segment overlay render stage — the real work). **B15** → **15a** (add
`target` to the log + guard `_dispatch_one:443` — zero-schema, high value, ship it)
and **15b** (durable record + repair — deferred pending the migration/outage design).

**Hidden dependencies not stated in the plan:**
- **B3 → B4.** B3's Red passes `context=DSL_HOOKS_CONTEXT`; it cannot be written
  before `CompileContext` exists. (Implementation Order sequences it correctly by
  luck of ordering, but never names the dependency.)
- **B2 → `CompositeDoc` model change.** B2 is listed as touching only `compile.py`;
  it must touch `composite.py` and `models.py` (BLOCKING-2).
- **B15b → a migration in another repository**, gated by `FEATURE_SCHEMA`
  (`pg.py:49-68`) with a 503-the-whole-feature blast radius (BLOCKING-3b).
- **B17 → CT-1 executing**, hence → ffmpeg. Not stated (BLOCKING-5).

---

## Suggested plan amendments

```diff
# D2
~ "the real enum is DiagnosticCode" → "DiagnosticCode is a Literal[...] type alias
  (models.py:143), not an Enum; emission sites use bare string literals
  (cf. compile.py:143 code="UNRESOLVED_HOLE")"
- B2 checkbox: "no literal "INVALID_MARKER" outside DiagnosticCode"   # not implementable

# D7  (rewrite the table row + R1)
~ CutInOverlay time base: "source-segment-relative" → "ABSOLUTE SOURCE TIME;
  build_overlay_filtergraph converts to segment-relative internally via
  _relative_window (overlays.py:307-316) and CLAMPS partial overlaps"
~ R1: drop "no valid representation"; boundary-spanning cut-ins are clamped to each
  overlapping segment by design. Keep CUTIN_INVALID only for cut-ins outside EVERY
  segment (silently dropped today at overlays.py:137-141).

# Behavior 2
~ Files touched: compile.py → composite.py + models.py (CompositeDoc) + compile.py
+ Add: CompositeDoc.invalid_markers (or a diagnostics sink) — read_composite
  swallows MarkerError at composite.py:100-101 and :123-125, so the compiler never
  sees the marker
+ Add: motivation — malformed markers are SILENTLY IGNORED today (latent defect)
- Remove "[insert" from the parametrize list (undiagnosable — never matches
  _MARKER_LINE_RE)

# Behavior 3
~ Green signature: _check_unsupported(doc, diagnostics)
                 → _check_unsupported(doc, diagnostics, *, context=None)
+ Add: context is None → return False (keeps tests/dsl/test_compile_unsupported.py
  :55/:85/:123 green — [insert relevant]/[insert file]/[find relevant] are SUPPORTED
  features, not oversights)
+ Add: unsupported set is per-workflow (DSL_HOOKS_UNSUPPORTED_VERBS), not global
+ Add: explicit dependency on B4

# Behavior 4
- test_context_defaults_none_preserves_today_behavior   # tautology; both sides are
                                                        # the same code path
~ "16 tests/dsl/* files" → "18 test_*.py files"

# Behavior 9  → SPLIT
+ B9a: pure cut-in → CutInOverlay mapper + CUTIN_INVALID for out-of-every-segment
+ B9b: per-segment overlay render stage at the WORKER seam (app.py), between
       download_segments (footage_stitch.py:88) and stitch_footage_reel (:318),
       via overlays.render_overlay_clip (overlays.py:200) — footage_stitch.py has
       NO per-segment file boundary (one filter_complex, one ffmpeg exec)
+ B9b: add a double-normalization test (render_overlay_clip re-encodes to
       1080x1920; footage_stitch then trims/scales/crops again at :191-198)
~ Files touched: footage_stitch.py → app.py (footage_stitch.py stays untouched)

# Behavior 14
+ Files touched: add web/server.py:260-270 (_poll_response_body)
+ Add: strip/redact `result` from the poll payload — payload = dict(cp_body) at :261
       leaks video_path verbatim; B14's own test asserts it must not
+ Add: _poll_response_body must accept a locally-derived error — :266-269 only
       sources `error` from _execution_error(cp_body), and the CP said "succeeded"
~ _is_browser_deliverable → delegate to _is_valid_url (reel_jobs.py:151-156);
  startswith accepts "https://" with no netloc
~ parity test → parametrize over sorted(ALLOWLISTED_TARGETS - DELIVERY_REQUIRED_TARGETS)
~ State once: status stays "failed"; delivery_unavailable is the error CODE

# Behavior 15  → SPLIT
+ B15a (this slice): add `target` to the existing log (server.py:342-346) + wrap
        _dispatch_one's attach (:443) so it stops breaching its _DispatchOutcome
        contract and aborting the fan-out at :468. Zero schema.
+ B15b (defer): durable record + repair. Blocked on (i) the outage paradox — the
        trigger IS db-unavailability, so a Postgres row is unreachable; and
        (ii) no migrations exist in this repo (web/pg.py:1-12: root-owned,
        migrations/deepresearch/ lives in the monorepo root). A new FEATURE_SCHEMA
        entry (pg.py:49-68) 503s the whole feature until the root migration lands;
        ci.yml has no migration gate.
+ Consider instead: a CP-reconciling sweep over mark_stale_queued's existing
  predicate (pg.py:372-387: status='queued' AND execution_id IS NULL) — the job row
  IS the durable record, and the reaper already exists.

# Behavior 17
~ Classify BLOCKING (R5 says it carries the CT-1/CT-2 closure guarantee)
+ Add _require_ffmpeg() fail-closed
+ Define REAL_WORKER_RESULT as the literal return of an in-test dsl_hooks_to_reels()
  invocation — not a constant (else the parity test is a tautology on day one)
+ Add the regeneration command to the plan

# CT-1
~ "both are pre-existing production injection points" → uploader is
  (app.py:1315-1316); fetch_segment is NEW (app.py has zero dsl coupling)
~ RED-AT-SEAM cut-in proof depends on B9's seam — revisit after B9 is re-cut

# Housekeeping
- Strip leaked tool XML at plan lines 1521-1523 (</content>, </invoke>)
```

## Suggested implementation order (revised)

1. **B4** `CompileContext` — unchanged; genuinely unblocks everything, and safer
   than claimed (`compile_composite` has zero production callers).
2. **B3** (context-gated) + **B6** — pure, cheap, correctly red.
3. **B2** (re-cut to `composite.py` + `CompositeDoc`) — now a real defect fix.
4. **B1, B9a** — fixtures + pure mapper.
5. **B7** — characterization.
6. **B9b** — the per-segment overlay render stage. **Do this before B16**; it is the
   riskiest work and the worker depends on its shape.
7. **B8, B16** — finish burn-in + worker (**CT-1 closes**).
8. **B17** (now BLOCKING) — parity fixture generated from CT-1's real output.
9. **B10–B13** — web target + fail-closed submit.
10. **B14** (incl. `_poll_response_body`) — delivery policy (**CT-2 closes**).
11. **B15a** — log `target` + guard `_dispatch_one`.
12. ~~B15b~~ — **deferred**; needs a migration/outage design decision.

The plan's own rationale for B14-after-B16 ("so the delivery policy is written
against the worker's real result shape") is good and is preserved.

---

## What the plan gets right (so it isn't lost in the rework)

- **D1, D4, D5, D6 are correct**, and the reconciliation discipline
  ("research assumed X → actual is Y → this plan does Z") is exactly right. The
  author demonstrably read the code; the errors that remain are in the two places
  where a docstring or a name misleads (D7's time base, D3's vestigial codes).
- **Scope discipline is exemplary** — zero A1 leakage, every deferral justified and
  risk-tracked.
- **Effect ordering is preserved** and verified against `server.py:310-350`.
- **The CT-1/CT-2 split is the right call** for an out-of-repo CP, and the
  fail-closed-vs-skipif distinction is correctly drawn and correctly applied.
- **D5's per-target scoping via `DELIVERY_REQUIRED_TARGETS`** is the right shape —
  it makes a risky global change into a safe local one.
- **B6's strengthening is verifiably additive** and cannot break `FootageReel`.
- **The target-id derivation constraint** (`dsl_hooks_to_reels`, not
  `reel_dsl_hooks_to_reels`) is correct and is the kind of detail that costs a day
  when missed.

---

## Approval status

- [ ] Ready for Implementation
- [ ] Needs Minor Revision
- [x] **Needs Major Revision** — 6 BLOCKING findings. B2, B9, and B15 rest on
      premises the code contradicts and need design decisions before tests can be
      written; B14's Green cannot satisfy B14's own tests; B17 must be gated;
      B3 breaks three green tests as specified. The remaining ~11 behaviors are
      sound and can proceed largely as written.

**Reviewer:** BlueBay · **Base verified:** `f72adb4` · **Method:** every `file:line`
claim re-read against the tree; no claim accepted from the plan or the research.

---

# Re-review (rework verification)

**Date:** 2026-07-15 · **Reviewer:** BlueBay · **Plan rev:** 2 (2039 lines, mtime 16:03)
**Scope:** verification only — prior findings re-checked against the reworked text, and
against real code at `f72adb4` wherever the fix is a code claim. Not a fresh review.
**Operator-approved descopes accepted as given:** B9b (cut-in render) and B15b (durable
orphan table) deferred to follow-up builds.

## Prior BLOCKING findings

| # | Finding | Status | Evidence in rev 2 |
|---|---|---|---|
| B-1 | B9 cut-in seam doesn't exist | **CLOSED** | B9a is pure mapper, no render, `footage_stitch.py` untouched (plan `:1286-1290`, `:1318`); B9b deferred with worker-level seam + double-normalization note (`:491-519`); D7 time base corrected to **ABSOLUTE SOURCE TIME** (`:284`, `:289-302`); R1 retired (`:1940-1951`) |
| B-2 | B2 in wrong file | **CLOSED** | Retargeted to `composite.py` + `models.py` (`CompositeDoc.invalid_markers`) (`:842-844`, `:867-877`); silent-failure defect named as the motivation (`:803-808`); parametrize narrowed — but see **N-1** |
| B-3 | B15 durable table unreachable + no migrations | **CLOSED** | B15b deferred with outage paradox + no-migrations + CP-reconciling-sweep note (`:520-544`); B15a keeps log+`target` and wraps `_dispatch_one` (`:1749-1753`); **verified: zero `deepresearch.orphaned_dispatch` references remain** and the MISSING table row is gone |
| B-4 | B3 breaks 3 green tests | **CLOSED** | `_check_unsupported(doc, diagnostics, *, context=None)` with `context is None → return False` (`:953-960`); workflow-keyed `DSL_HOOKS_UNSUPPORTED_VERBS` (`:951`, `:981-983`); B3→B4 dependency named (`:905-907`, `:1991`, `:2011`); the three tests pinned by file:line as the regression proof (`:988-989`) |
| B-5 | B17 ungated | **CLOSED** | Classified **BLOCKING** (`:1845-1853`, `:669-675`); `REAL_WORKER_RESULT` is a **live `await dsl_hooks_to_reels(...)`** (`:1887`) with a grep-for-literals criterion (`:1928-1929`); `_require_ffmpeg()` fail-closed (`:1885`); `--regenerate-golden` command (`:1901-1908`); false composite precedent explicitly retracted (`:1910-1915`) |
| B-6 | B14 Green can't pass B14's tests | **CLOSED** | `_poll_response_body` (`server.py:260-270`) in Files Touched (`:1590-1591`); **whole `result` dict** stripped, with its own test (`:1625-1631`, `:1668-1670`); locally-derived error param (`:1600-1604`) |

## Prior SHOULD-FIX findings

| # | Finding | Status | Evidence |
|---|---|---|---|
| SF-1 | B4 tautology test | **CLOSED** | Deleted with rationale (`:1028-1037`); D1 proof correctly rests on "all 18 `tests/dsl/test_*.py` green" (`:1071-1072`) |
| SF-2 | `fetch_segment` not pre-existing | **CLOSED** | CT-1 now distinguishes provenance: `uploader` pre-existing, `fetch_segment` **NEW — created by this slice** (`:697-707`) |
| SF-3 | Parity covered 1 of 4 targets | **CLOSED** | Parametrized over `sorted(ALLOWLISTED_TARGETS - DELIVERY_REQUIRED_TARGETS)` (`:1642-1650`, `:1682-1683`) |
| SF-4 | B14 duplicated URL validation | **CLOSED** | `_is_browser_deliverable` delegates to `_is_valid_url` (`:1658-1661`); `"https://"` no-netloc case tested (`:1633`) |
| SF-5 | `delivery_unavailable` status-vs-code | **CLOSED** | Stated as an **error CODE**, status stays `failed` (`:1655`, `:1621`, `:1679-1681`) |
| SF-6 | D5/D6 line refs off by 2 | **CLOSED** | `:228-231` / `:232-235` corrected (`:1584`); `_dispatch_one` def `:424` vs call `:443` disambiguated (`:1715-1716`) |

NICE-TO-HAVEs also closed: trailing tool XML stripped (file now ends cleanly at
References); "16 `tests/dsl`" → **18** throughout (`:895`, `:990`, `:1071`); D2's
unimplementable "no literal outside `DiagnosticCode`" checkbox removed with rationale
(`:119`, `:885-888`).

## Rework-introduced issues (both new, neither blocking)

**N-1 — SHOULD-FIX: B2's second parametrize case is not a `MarkerError` and will
fail even after a correct Green.** Plan `:850`:
```python
@pytest.mark.parametrize("text", ["[bogus 1.0]", "[insert relevant ? => nope 5]"])
```
Executed against `f72adb4`:
```
'[bogus 1.0]'                   -> MarkerError: unknown verb: 'bogus'   | markers=0   ✅
'[insert relevant ? => nope 5]' -> PARSES OK  kind=insert               | markers=1   ❌
```
`? => value` is **valid DSL v2** — a resolved hole, exactly the resolver's audit-trail
format (`resolver.py:55-111`; `serialize_marker` "re-emits `?` / `=> value`",
`parser.py:261-272`). It lands in `doc.markers`, never `doc.invalid_markers`, so
`assert doc.invalid_markers` (`:853`) fails for this case regardless of implementation.

Mildly ironic: rev 2 correctly removed two coin-flip cases (`"[insert"`,
`"[trans notaprimitive 1.0]"`) for this exact reason and then added a third that is
not a coin flip but a definite miss. **Remediation:** drop it, or use a verified
raiser — both of these were confirmed to raise `MarkerError` with `markers=0` at
`f72adb4`:
- `"[]"` → `MarkerError: not a bracketed marker: '[]'`
- `"[trans 1.0]"` → `MarkerError: unknown transition primitive: '1.0'`

(Incidental: `[trans <bad-primitive>]` **does** raise `MarkerError` at parse time, so
rev 2's stated worry that it "may parse cleanly and fail later as `INVALID_TRANSITION`"
(`:833-837`) is over-cautious — it is a legitimate `INVALID_MARKER` case. Removing it
was harmless; it can be restored if a second case is wanted.)

**N-2 — NICE-TO-HAVE: B15a asserts a `_DispatchOutcome` attribute that does not
exist.** Plan `:1742`: `assert outcome.disposition == "attach_failed"`. The real class
(`web/server.py:404-409`):
```python
def __init__(self, ok: bool, execution_id: str | None, outcome: str):
    self.ok, self.execution_id, self.outcome = ok, execution_id, outcome
```
The field is **`.outcome`**, not `.disposition` — the test would raise `AttributeError`
rather than assert. Understandable slip: "disposition" is the *prose* term in the
function's own docstring (`:426`). **Remediation:** `outcome.outcome == "attach_failed"`.

Both are one-token test-authoring fixes that fail **loudly and immediately** at
implementation. Neither is a false-green, neither reflects a design defect, and neither
blocks: an implementer hits them in the first Red run and fixes them in seconds.

## New-inconsistency sweep (requested)

- **Dangling references to deferred work — none.** No behavior depends on B9b or B15b.
  The mapper is not orphaned: the production chain consumes it at `:652`
  (`map_cut_ins (B9a: validate only, NO render — B9b deferred)`), and B9a's value
  is fail-closed rejection (`CUTIN_INVALID`), which needs no render.
- **Dangling closure legs — none.** CT-1's rev-1 cut-in RED-AT-SEAM proof (which
  presupposed B9b's seam) is explicitly removed (`:711-712`); the `upload_reel → None`
  proof survives and is independently valid (`storage.py:43-73` returns `None` when
  unconfigured). CT-1's FORBIDDEN SPAN still lists "the cut-in mapper" (`:696`) —
  correct and consistent, since B9a exists and the worker calls it.
- **Implementation order — coherent.** B4→B3 dependency now explicit (`:1991`,
  `:2011`); B17 correctly follows B16 (`:1996-1997`) since it executes the worker;
  B14 after B16 rationale preserved; B9b/B15b struck through as deferred
  (`:2001-2002`). No step depends on a deferred item.
- **B8 unaffected by the B9b descope** — it burns *image* cut-ins via `finish`'s
  separate `image_cutins` path (`finish.py:107-108`/`:264`), which D7 correctly keeps
  distinct from the deferred `overlays` path. Not a contradiction.
- **New code claims spot-checked and VERIFIED:** `server.py:506` carousel unguarded
  attach (`:1774`) — confirmed, `deps.carousels.attach_execution_id(...)` is unguarded,
  same shape; correctly scoped as a follow-up bead, not this slice.
- **R6 added** (`:1977-1982`) — honestly records that both deferrals are load-bearing
  for the product, not cosmetic. Good addition; it's the thing most likely to be
  forgotten.

## Re-review verdict: **READY-TO-IMPLEMENT**

All 6 BLOCKING and all 6 SHOULD-FIX findings are closed, verified against the reworked
text and re-checked against `f72adb4` where the fix was a code claim. The two descopes
are handled the right way round: the deferred work is documented with the design
already worked out (worker-level seam + double-normalization hazard for B9b; the
CP-reconciling sweep for B15b), so the follow-up starts from fact rather than
rediscovery.

The rework is notably better than a minimal patch — it internalized *why* each finding
held (D7's correction propagates into D7, B9a, R1 and the closure map; B15b's outage
paradox is explained rather than just obeyed) and its self-corrections are stated
plainly rather than quietly. Residual risk is two bad test-case literals (N-1, N-2)
that fail loudly on first run. Neither warrants another review cycle.

**Residual blockers: none.**
