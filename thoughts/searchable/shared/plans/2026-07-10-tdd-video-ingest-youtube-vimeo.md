# Video Ingest Hardening (YouTube + Vimeo) — TDD Implementation Plan

## Overview

`reel-af`'s real-footage ingest (`download_crisp_source` → `build_crisp_ytdlp_command`) only ever
attempts a **YouTube-itag "crisp" download** (`137+140/137+bestaudio[ext=m4a]`) with no JS runtime
and no cookies. On the headless Railway deploy this fails two ways:

1. **YouTube:** `yt-dlp` errors with *"No supported JavaScript runtime could be found… add
   --js-runtimes"* and *"Sign in to confirm you're not a bot. Use --cookies-from-browser or
   --cookies"*.
2. **Vimeo / other hosts:** itags `137`/`140` don't exist off YouTube, so the selector can't match.

This plan makes the argv **host-aware** (YouTube keeps the crisp itag ladder + deno + optional
cookies; Vimeo/generic get a portable best-quality ladder) using TDD, keeping the pure
`build_crisp_ytdlp_command` fully unit-testable with no network.

## Current State Analysis

- `src/reel_af/render/hooks.py`
  - `CRISP_YTDLP_FORMAT = "137+140/137+bestaudio[ext=m4a]"` (line 17), `YTDLP_MERGE_OUTPUT_FORMAT = "mp4"` (line 18).
  - `build_crisp_ytdlp_command(source_url, output_path, *, format_selector=CRISP_YTDLP_FORMAT, merge_output_format="mp4") -> list[str]` (line 54). Returns `["yt-dlp","-f",<fmt>,"--merge-output-format","mp4","-o",<out>,<url>]`.
  - `download_crisp_source(source_url, output_path, *, timeout_s=None, runner=subprocess.run) -> Path` (line 79). Runs the argv; on `returncode != 0` raises `RuntimeError("yt-dlp crisp download failed (exit N): <stderr[-1200:]>")`.
  - `__all__` exports both (lines 552–553).
- **Call sites (unchanged by this plan — they call `download_crisp_source(url, dest)`):**
  - `src/reel_af/app.py:635` (the `reel_composite_to_reel` reasoner).
  - `src/reel_af/render/composite_pipeline.py:99` (`asyncio.to_thread(hooks.download_crisp_source, …)`).
  - `src/reel_af/cli.py:378` (`reel-af reels` `_resolve_source`).
- **Dockerfile:** installs `ffmpeg`, `nodejs`, `chromium`, `yt-dlp`, `uv`. **No `deno`.**

### Key Discoveries
- **Existing test (backward-compat anchor):** `tests/test_hooks.py:26` `test_crisp_ytdlp_command_uses_vertical_native_format` asserts, for `https://youtu.be/example`:
  - `cmd[:2] == ["yt-dlp", "-f"]` → **`-f` must remain the 2nd element** (new flags append AFTER the format ladder).
  - `CRISP_YTDLP_FORMAT in cmd` and `CRISP_YTDLP_FORMAT == "137+140/137+bestaudio[ext=m4a]"` → **YouTube must keep the itag ladder**.
  - `"height<=1080" not in " ".join(cmd)` → **the generic ladder must NOT leak into YouTube commands**.
  - This test must pass **unchanged**; it already encodes the host-aware contract for the YouTube branch.
- **Test framework:** `pytest` + `pytest-asyncio` (`[tool.pytest.ini_options] testpaths=["tests"]`). Run: `uv run pytest tests/test_hooks.py`.
- **Fake-runner pattern already exists:** `download_crisp_source(..., runner=...)` — tests inject a fake `runner` returning a `returncode`/`stderr` object; no subprocess/network.
- **Config style:** `CRISP_YTDLP_FORMAT` is a **named module constant** (not JSON). New ladders/flags follow the same pattern (named constants in `hooks.py`) — satisfies §10 "not inline" while matching the immediate convention; JSON is unnecessary since format ladders are code-adjacent, not user-tunable.

## Desired End State

`build_crisp_ytdlp_command(url, out)` returns an argv correct for the URL's host, and
`download_crisp_source` runs it and gives an actionable error when a download is gated. Deno is in
the image; a cookies file is supplied via `YTDLP_COOKIES_FILE` (Railway secret/volume) for gated
YouTube. Non-gated URLs (many Vimeo, direct files) work with no cookies configured.

### Observable Behaviors
- Given a YouTube URL, the argv uses the crisp itag ladder AND `--js-runtimes deno`.
- Given a Vimeo/generic URL, the argv uses the portable `height<=1080` best ladder and NO `--js-runtimes`.
- Given `YTDLP_COOKIES_FILE` set to an existing file, the argv includes `--cookies <file>`; unset → no `--cookies`.
- Given a gated-download failure, `download_crisp_source` raises a `RuntimeError` whose message names the cookies remedy.

## What We're NOT Doing
- Not adding `--cookies-from-browser` (no browser on the server).
- Not solving arbitrary anti-bot beyond a supplied cookies file (YouTube may still rate-limit).
- Not changing the reasoner / CLI / pipeline call sites (they keep calling `download_crisp_source(url, dest)`).
- Not migrating format ladders into `config/*.json` (kept as named constants — see Key Discoveries).
- Not adding new source hosts beyond youtube/vimeo/generic classification.
- Not building a real-network integration test in CI (kept as a manual/deploy check).

## Testing Strategy
- **Framework:** `pytest` (+ `pytest-asyncio` where needed). New tests live in `tests/test_hooks.py` (extend) or a focused `tests/test_ingest.py`.
- **Unit (all behaviors here):** `build_crisp_ytdlp_command` is pure → assert argv per host with NO network. `download_crisp_source` uses the injected `runner` (fake object with `returncode`/`stderr`) → assert error mapping with NO subprocess.
- **Property:** for a corpus of host samples, `-f` is always at index 1 and the argv always ends with `[..., "-o", <out>, <url>]` (invariants that must hold for every host).
- **Manual / integration (documented, not automated):** deno in the image; a real gated YouTube URL downloads once `YTDLP_COOKIES_FILE` is mounted; a real Vimeo URL downloads with no cookies.

## Workflow Closure

**No BLOCKING closure test applies.** Every behavior is **LEAF**:
- `_classify_host` and `build_crisp_ytdlp_command` are **pure functions** (string → string / string → argv) — same module, no async edge, no cross-module registration, no read-model updated by a separate process. A unit test fully observes them.
- `download_crisp_source` invokes an **injected `runner`** synchronously in the same module; the "X updates Y across a process boundary" shape does not occur (the only cross-process actor is the external `yt-dlp`/network, which is out of unit scope and covered by the manual/deploy check).
- Rationale + rule: `references/closure-test-framework.md` — closure targets cross-process read-models; a pure builder + injected-subprocess wrapper is LEAF by the same-module/no-async/no-registration test.

The real end-to-end "a YouTube URL produces a reel" is verified **manually on the deploy** (deno + cookies secret), recorded in `deploy/RAILWAY-RUNBOOK.md` §7; this plan does not imply that network path is unit-covered.

---

## Behavior 1: Host classification

### Test Specification
**Given** a source URL, **when** classified, **then** it returns `"youtube"`, `"vimeo"`, or `"generic"`.

- youtube: `youtube.com/watch?v=…`, `youtu.be/…`, `m.youtube.com/…`, `www.youtube.com/…`, `music.youtube.com/…`
- vimeo: `vimeo.com/…`, `player.vimeo.com/…`
- generic: anything else (`https://cdn.example.com/v.mp4`, Loom, etc.)

**Edge Cases:** scheme-less / uppercase host / trailing query+fragment / empty string (→ `ValueError`, matching `build_crisp_ytdlp_command`'s existing empty-url guard) / a bare filename path.

**Property:** classification depends only on the URL **host**, case-insensitively — `classify(u) == classify(u.upper_host())` for all samples.

**Files touched:** `src/reel_af/render/hooks.py` (add `_classify_host`), `tests/test_hooks.py` (or `tests/test_ingest.py`).

### 🔴 Red
```python
# tests/test_ingest.py
import pytest
from reel_af.render.hooks import _classify_host

@pytest.mark.parametrize("url,host", [
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://m.youtube.com/watch?v=abc", "youtube"),
    ("https://vimeo.com/123456", "vimeo"),
    ("https://player.vimeo.com/video/123456", "vimeo"),
    ("https://cdn.example.com/clip.mp4", "generic"),
    ("HTTPS://YOUTU.BE/AbC", "youtube"),
])
def test_classify_host(url, host):
    assert _classify_host(url) == host

def test_classify_host_rejects_empty():
    with pytest.raises(ValueError):
        _classify_host("")
```

### 🟢 Green
```python
from urllib.parse import urlparse

YOUTUBE_HOSTS = ("youtube.com", "youtu.be")   # matched as suffixes
VIMEO_HOSTS = ("vimeo.com",)

def _classify_host(source_url: str) -> str:
    source_url = str(source_url).strip()
    if not source_url:
        raise ValueError("source_url is required")
    host = (urlparse(source_url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) or host.endswith(h) for h in YOUTUBE_HOSTS):
        return "youtube"
    if any(host == h or host.endswith("." + h) for h in VIMEO_HOSTS):
        return "vimeo"
    return "generic"
```

### 🔵 Refactor
- [ ] No duplication: single suffix-match helper `_host_matches(host, candidates)` used by both branches.
- [ ] Reveals intent: `YOUTUBE_HOSTS` / `VIMEO_HOSTS` named; the function reads as a lookup.
- [ ] Complexity down: one parse, two membership checks, default.
- [ ] Fits patterns: module-level constants like `CRISP_YTDLP_FORMAT`.

### Success Criteria
**Automated:** Red fails (no `_classify_host`); Green passes `uv run pytest tests/test_ingest.py -k classify`; `uv run ruff check` clean.
**Manual:** n/a (pure).

---

## Behavior 2: Host-aware format ladder

### Test Specification
**Given** a URL, **when** building the argv, **then** `-f` uses the crisp itag ladder for youtube and the portable `height<=1080` ladder for vimeo/generic.

**Edge Cases:** explicit `format_selector=` arg still overrides host default (back-compat — keep the kwarg); generic host with no video streams is a runtime yt-dlp failure (Behavior 5), not a build concern.

**Property:** `-f` is always at argv index 1; the selected ladder string appears exactly once.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_ingest.py`.

### 🔴 Red
```python
from reel_af.render.hooks import (
    build_crisp_ytdlp_command, CRISP_YTDLP_FORMAT, GENERIC_YTDLP_FORMAT,
)

def test_youtube_uses_itag_ladder(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    assert cmd[1] == "-f" and cmd[2] == CRISP_YTDLP_FORMAT
    assert "height<=1080" not in " ".join(cmd)

def test_vimeo_uses_generic_ladder(tmp_path):
    cmd = build_crisp_ytdlp_command("https://vimeo.com/123", tmp_path / "s.mp4")
    assert cmd[1] == "-f" and cmd[2] == GENERIC_YTDLP_FORMAT
    assert GENERIC_YTDLP_FORMAT == "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"

def test_explicit_format_selector_overrides_host(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4", format_selector="18")
    assert cmd[2] == "18"
```

### 🟢 Green
```python
GENERIC_YTDLP_FORMAT = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
_FORMAT_BY_HOST = {"youtube": CRISP_YTDLP_FORMAT, "vimeo": GENERIC_YTDLP_FORMAT}

def build_crisp_ytdlp_command(source_url, output_path, *, format_selector=None, merge_output_format=YTDLP_MERGE_OUTPUT_FORMAT):
    source_url = str(source_url).strip()
    if not source_url:
        raise ValueError("source_url is required")
    host = _classify_host(source_url)
    fmt = format_selector or _FORMAT_BY_HOST.get(host, GENERIC_YTDLP_FORMAT)
    cmd = ["yt-dlp", "-f", fmt, "--merge-output-format", merge_output_format, "-o", str(Path(output_path)), source_url]
    return cmd
```
> **Back-compat note:** the signature default changes from `format_selector=CRISP_YTDLP_FORMAT` to `format_selector=None` (host-derived). The existing YouTube test still passes (youtube → `CRISP_YTDLP_FORMAT`). Any caller that relied on the old default for a *non-YouTube* URL now gets the correct generic ladder — an intended fix.

### 🔵 Refactor
- [ ] No duplication: `_FORMAT_BY_HOST` table, not `if/elif` per host.
- [ ] Intent: host → ladder is a data lookup; the builder is a template.
- [ ] Complexity down: host + flags computed by small helpers, assembled once (sets up Behaviors 3–4).
- [ ] No shallow wrapper: keep the flag assembly inside the builder (deep function), not scattered.

### Success Criteria
**Automated:** the two new tests pass; **`tests/test_hooks.py::test_crisp_ytdlp_command_uses_vertical_native_format` still passes unchanged**; `ruff` clean.
**Manual:** n/a.

---

## Behavior 3: YouTube JS runtime (deno)

### Test Specification
**Given** a YouTube URL, **when** building the argv, **then** it contains `--js-runtimes deno`; for vimeo/generic it does not.

**Edge Cases:** flag must land AFTER the format (so `cmd[:2] == ["yt-dlp","-f"]` holds); order among appended flags is irrelevant to yt-dlp but pinned by tests for determinism.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_ingest.py`, **`Dockerfile`** (install deno).

### 🔴 Red
```python
def test_youtube_adds_deno_runtime(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    i = cmd.index("--js-runtimes")
    assert cmd[i + 1] == "deno"
    assert cmd.index("-f") < i        # appended after the format

def test_generic_has_no_js_runtime(tmp_path):
    cmd = build_crisp_ytdlp_command("https://vimeo.com/1", tmp_path / "s.mp4")
    assert "--js-runtimes" not in cmd
```

### 🟢 Green
```python
YOUTUBE_JS_RUNTIME = "deno"
# inside build_crisp_ytdlp_command, after the base cmd:
if host == "youtube":
    cmd += ["--js-runtimes", YOUTUBE_JS_RUNTIME]
```
**Dockerfile (integration, not a unit test):** install deno so `--js-runtimes deno` resolves:
```dockerfile
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno
# (or apt/npm equivalent; verify `deno --version` in the image)
```

### 🔵 Refactor
- [ ] No duplication: host-specific flags built by one `_host_flags(host, cookies_file)` helper returning a list, appended once.
- [ ] Intent: `_host_flags` is the single place host quirks live.

### Success Criteria
**Automated:** new tests pass; YouTube back-compat test still green (flag is after `-f`); `ruff` clean.
**Manual:** `docker build` then `docker run … deno --version` succeeds; a real YouTube fetch no longer prints the "No supported JavaScript runtime" warning.

---

## Behavior 4: Cookies when configured

### Test Specification
**Given** `YTDLP_COOKIES_FILE` set to an **existing** file, **when** building the argv, **then** it contains `--cookies <file>`. **Given** it is unset, no `--cookies`. **Given** it is set but the file is **missing**, raise a clear config error.

**Edge Cases:** cookies apply to all hosts (Vimeo can also gate), but are only *needed* for gated content — always add when configured. Empty-string env == unset.

**Property:** `--cookies` present ⇔ (`YTDLP_COOKIES_FILE` is set AND the path exists).

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_ingest.py`.

### 🔴 Red
```python
def test_cookies_added_when_file_configured(tmp_path, monkeypatch):
    ck = tmp_path / "cookies.txt"; ck.write_text("# netscape")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(ck))
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    assert cmd[cmd.index("--cookies") + 1] == str(ck)

def test_no_cookies_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    assert "--cookies" not in cmd

def test_configured_but_missing_cookies_file_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(tmp_path / "nope.txt"))
    with pytest.raises(ValueError, match="YTDLP_COOKIES_FILE"):
        build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
```

### 🟢 Green
```python
def _cookies_file() -> str | None:
    p = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if not p:
        return None
    if not Path(p).is_file():
        raise ValueError(f"YTDLP_COOKIES_FILE set but not a file: {p!r}")
    return p
# inside the builder, in _host_flags or after base cmd:
ck = _cookies_file()
if ck:
    cmd += ["--cookies", ck]
```
> Optional variant (documented, not required): `YTDLP_COOKIES_B64` → decode to a temp file at process start; keeps Railway secrets as env, not volumes. If added, it resolves into the same `_cookies_file()` path.

### 🔵 Refactor
- [ ] No duplication: single `_cookies_file()` used by the builder; env read in one place.
- [ ] Intent: precedence (explicit file → none) obvious; error message names the env var.

### Success Criteria
**Automated:** three tests pass; unset-env test guarantees non-gated URLs still build cookie-free; `ruff` clean.
**Manual:** with a real exported cookies file mounted at `YTDLP_COOKIES_FILE`, a gated YouTube URL downloads on the deploy.

---

## Behavior 5: Actionable error on gated failure

### Test Specification
**Given** a `runner` that returns `returncode != 0` with stderr containing the bot-check text, **when** `download_crisp_source` runs, **then** it raises `RuntimeError` whose message includes both the yt-dlp stderr AND a remedy hint naming `YTDLP_COOKIES_FILE`. Generic failures keep today's message.

**Edge Cases:** stderr mentions "js runtime"/"deno" → hint also mentions the deno/image requirement; success path (`returncode == 0`) returns the target Path unchanged (existing behavior).

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_ingest.py`.

### 🔴 Red
```python
from reel_af.render.hooks import download_crisp_source

class _Proc:
    def __init__(self, rc, err): self.returncode, self.stderr = rc, err

def test_gated_failure_hints_cookies(tmp_path):
    def runner(cmd, **kw): return _Proc(1, "ERROR: Sign in to confirm you're not a bot. Use --cookies")
    with pytest.raises(RuntimeError, match="YTDLP_COOKIES_FILE"):
        download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)

def test_success_returns_target(tmp_path):
    def runner(cmd, **kw): return _Proc(0, "")
    out = download_crisp_source("https://vimeo.com/1", tmp_path / "s.mp4", runner=runner)
    assert out == tmp_path / "s.mp4"
```

### 🟢 Green
```python
_BOT_MARKERS = ("sign in to confirm", "not a bot", "--cookies")
def download_crisp_source(source_url, output_path, *, timeout_s=None, runner=subprocess.run):
    target = Path(output_path); target.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_crisp_ytdlp_command(source_url, target)
    proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
    if getattr(proc, "returncode", 0) != 0:
        stderr = str(getattr(proc, "stderr", ""))
        hint = ""
        if any(m in stderr.lower() for m in _BOT_MARKERS):
            hint = " — gated by the host; set YTDLP_COOKIES_FILE to a valid cookies export."
        raise RuntimeError(f"yt-dlp crisp download failed (exit {getattr(proc,'returncode','unknown')}): {stderr[-1200:]}{hint}")
    return target
```

### 🔵 Refactor
- [ ] No duplication: marker list named; hint logic one branch.
- [ ] Intent: error names the remedy the operator can act on.
- [ ] Complexity down: same structure as today, one hint branch added.

### Success Criteria
**Automated:** both tests pass; existing `download_crisp_source` behavior (raise on non-zero, return target on zero) preserved; `ruff` clean.
**Manual:** a real gated failure on the deploy shows the hint in the execution error.

---

## Behavior 6: Backward compatibility + integration wiring

### Test Specification
**Given** the full suite, **when** run, **then** all pre-existing tests pass (esp. `test_hooks.py::test_crisp_ytdlp_command_uses_vertical_native_format` and `tests/test_reels_cli.py`), and the reasoner/CLI/pipeline call sites are untouched.

**Files touched:** none new (verification gate); `Dockerfile` (deno, from Behavior 3); `deploy/RAILWAY-RUNBOOK.md` §7 (cookies-secret + deno steps).

### Success Criteria
**Automated:**
- [ ] `uv run pytest` fully green (no regressions), except the pre-known env-only failures already documented.
- [ ] `uv run ruff check src/reel_af/render/hooks.py tests/` clean.
- [ ] No new duplication in `hooks.py` ingest helpers (grep the appended-flags block appears once).

**Manual (deploy):**
- [ ] `Dockerfile` builds with deno; `deno --version` present in the image.
- [ ] `railway variables --service reel-af --set 'YTDLP_COOKIES_FILE=/app/secrets/cookies.txt'` + mount the cookies export (secret/volume).
- [ ] A real YouTube URL submitted via the UI reaches `succeeded` (or at least gets past the download stage).
- [ ] A real Vimeo URL downloads with **no** cookies configured.
- [ ] Runbook §7 updated: YouTube ingest = solved-with-cookies; Vimeo/generic = works.

---

## Integration & E2E Testing
- **Integration (deploy):** rebuild `reel-af` image (deno), set `YTDLP_COOKIES_FILE` secret, POST a YouTube + a Vimeo URL through the public UI, poll to `succeeded`.
- **No CI network test** — real downloads depend on host cookies/anti-bot and would be flaky; the argv contract is fully covered by unit tests, and the network leg is a manual deploy check.

## Order of Implementation
1 → 2 → 3 → 4 → 5 → 6. Behavior 1 (classification) unlocks 2–4; 5 is independent of build changes; 6 is the regression + deploy gate.

## References
- Target: `src/reel_af/render/hooks.py:17,54,79`
- Back-compat anchor test: `tests/test_hooks.py:26`
- Call sites: `src/reel_af/app.py:635`, `src/reel_af/render/composite_pipeline.py:99`, `src/reel_af/cli.py:378`
- Deploy context / follow-up: `deploy/RAILWAY-RUNBOOK.md` §7; bead `A1_workspace-blueprint-gm9`
- Memory: `youtube-download-ejs-fix` (deno + cookies), `reel-af-railway-deploy`
