# Video Ingest Hardening (YouTube + Vimeo) — TDD Implementation Plan

## Overview

`reel-af`'s real-footage ingest (`download_crisp_source` -> `build_crisp_ytdlp_command`) only ever
attempts a **YouTube-itag "crisp" download** (`137+140/137+bestaudio[ext=m4a]`) with no JS runtime
and no cookies. On the headless Railway deploy this fails two ways:

1. **YouTube:** `yt-dlp` errors with *"No supported JavaScript runtime could be found... add
   --js-runtimes"* and *"Sign in to confirm you're not a bot. Use --cookies-from-browser or
   --cookies"*.
2. **Vimeo / other hosts:** itags `137`/`140` don't exist off YouTube, so the selector can't match.

This plan makes ingest host-aware without hiding environment or filesystem state in the command
builder. `build_crisp_ytdlp_command(...)` remains deterministic and unit-testable: URL
normalization, host classification, format selection, and explicit `cookies_file=` input produce
one argv. `download_crisp_source(...)` owns environment/file resolution, default timeout behavior,
partial-output cleanup, subprocess execution, and actionable error messages.

## Current State Analysis

- `src/reel_af/render/hooks.py`
  - `CRISP_YTDLP_FORMAT = "137+140/137+bestaudio[ext=m4a]"` and
    `YTDLP_MERGE_OUTPUT_FORMAT = "mp4"` at lines 17-18.
  - `build_crisp_ytdlp_command(source_url, output_path, *, format_selector=CRISP_YTDLP_FORMAT,
    merge_output_format="mp4") -> list[str]` at line 54. Returns
    `["yt-dlp", "-f", <fmt>, "--merge-output-format", "mp4", "-o", <out>, <url>]`.
  - `download_crisp_source(source_url, output_path, *, timeout_s=None, runner=subprocess.run) -> Path`
    at line 79. It already has the injected `runner` test hook, but real call sites pass no timeout.
  - On non-zero `returncode`, it raises
    `RuntimeError("yt-dlp crisp download failed (exit N): <stderr[-1200:]>")`; the `1200` tail limit
    is currently unnamed.
  - `__all__` currently exports `CRISP_YTDLP_FORMAT`, `YTDLP_MERGE_OUTPUT_FORMAT`,
    `build_crisp_ytdlp_command`, and `download_crisp_source`.
- **Call sites keep the same download signature** (`download_crisp_source(url, dest)`):
  - `src/reel_af/app.py:635` in `_run_composite_reels`.
  - `src/reel_af/render/composite_pipeline.py:99` in `_crisp_ingest`.
  - `src/reel_af/cli.py:378` in `_resolve_source`.
- **Dockerfile:** installs `ffmpeg`, `nodejs`, `chromium`, `yt-dlp`, and `uv`; it does not install
  `deno`, and it does not install `unzip`/`7z` for the Deno installer path.
- **Runbook:** `deploy/RAILWAY-RUNBOOK.md` §7 names deno + cookies as the known YouTube ingest
  follow-up, but it does not specify cookie format, mount path, validation commands, or missing /
  expired-cookie failure behavior.

### Key Discoveries

- **Backward-compat anchor:** `tests/test_hooks.py:26`
  `test_crisp_ytdlp_command_uses_vertical_native_format` asserts, for `https://youtu.be/example`:
  - `cmd[:2] == ["yt-dlp", "-f"]`; `-f` must remain at index 1.
  - `CRISP_YTDLP_FORMAT` stays exactly `137+140/137+bestaudio[ext=m4a]`.
  - `"height<=1080" not in " ".join(cmd)`; the generic ladder must not leak into YouTube.
  - This test must pass unchanged.
- **Test framework:** `pytest` + `pytest-asyncio`; focused command:
  `uv run pytest tests/test_hooks.py`.
- **Fake runner pattern:** `download_crisp_source(..., runner=...)` already supports offline tests for
  return codes, stderr, timeout propagation, and command capture.
- **Config style:** yt-dlp format ladders and JS runtime are protocol selectors, not user preferences.
  Keep them as named module constants beside `CRISP_YTDLP_FORMAT`. Do not move them to JSON.
- **Failure surface:** app validation failures usually return `{"error": ...}`, but current ingest
  exceptions from `download_crisp_source` are not translated by `_run_composite_reels`. The UI shows
  failed execution errors from `j.error || j.result`; this plan makes app ingest failures explicit.

## Desired End State

`build_crisp_ytdlp_command(url, out)` returns argv correct for the URL host. YouTube keeps the crisp
itag ladder and adds `--js-runtimes deno`; Vimeo and generic HTTPS URLs use a portable
`height<=1080` ladder and no JS runtime flag. `download_crisp_source` resolves
`YTDLP_COOKIES_FILE`, applies a bounded default timeout, deletes partial target files on failure or
timeout, and raises an actionable `RuntimeError`. The app reasoner catches ingest errors and returns
`{"error": ...}` so the UI receives the same operator hint.

### Observable Behaviors

- Given a YouTube URL, the argv uses the crisp itag ladder and `--js-runtimes deno`.
- Given a Vimeo/generic HTTPS URL, the argv uses the portable `height<=1080` ladder and no
  `--js-runtimes`.
- Given lookalike hosts such as `notyoutube.com`, `evil-youtube.com`, `youtube.com.evil.test`, or
  `notvimeo.com`, classification is `generic`.
- Given scheme-less YouTube/Vimeo host-like input such as `youtu.be/x` or `www.youtube.com/watch?v=x`,
  the builder normalizes it to `https://...` before classifying and before placing it in argv.
- Given empty input or a path-only string such as `clip.mp4`, the builder raises `ValueError`.
- Given explicit `cookies_file=...`, the builder appends `--cookies <file>` without reading env or
  checking the filesystem.
- Given `YTDLP_COOKIES_FILE` set to an existing file, `download_crisp_source` passes that file into
  the builder; unset or empty env means no cookies; missing configured file raises `RuntimeError`
  before invoking the runner.
- Given bot-gate or JS-runtime yt-dlp failures, `download_crisp_source` includes the stderr tail plus
  a cookies or deno/image hint. Generic failures keep today's message shape plus the named stderr
  tail limit.
- Given non-zero return or timeout, the target and known yt-dlp `.part` sibling are removed before
  the error is raised.
- Given the app reasoner path, ingest errors surface as `{"error": "<hinted message>"}`.

## What We're NOT Doing

- Not adding `--cookies-from-browser`; there is no browser cookie store on the server.
- Not solving arbitrary anti-bot beyond a supplied cookies file; YouTube may still rate-limit or
  expire cookies.
- Not changing the public call signature at call sites; callers continue using
  `download_crisp_source(url, dest)`.
- Not replacing the synchronous `subprocess.run` wrapper with an async subprocess implementation in
  this plan. Coroutine cancellation remains best-effort because app/pipeline calls run the download
  in a thread; the default yt-dlp timeout is the hard bound for the child process.
- Not migrating format ladders into `config/*.json`; they stay as named protocol constants.
- Not adding new first-class source hosts beyond youtube/vimeo/generic classification.
- Not adding real-network CI tests; deploy/manual checks cover real hosts.

## Testing Strategy

- **Framework:** `pytest` (+ `pytest-asyncio` where existing tests require it). Prefer extending
  `tests/test_hooks.py`; a focused `tests/test_ingest.py` is acceptable if the file grows too large.
- **Unit:** `_normalize_source_url`, `_classify_host`, `_host_matches`, and
  `build_crisp_ytdlp_command` are pure and tested with no network, env, or filesystem dependency
  except `tmp_path` for output paths.
- **Wrapper:** `download_crisp_source` is tested with injected fake runners and `monkeypatch`ed env.
  These tests cover env/file resolution, default timeout propagation, non-zero returns, timeout
  errors, partial-output cleanup, and hint mapping without spawning `yt-dlp`.
- **App boundary:** one app-level test patches `download_crisp_source` to raise and asserts
  `_run_composite_reels` returns `{"error": ...}` with the hint.
- **Properties:** `-f` is always at argv index 1; the selected ladder appears exactly once; argv ends
  with `["-o", <out>, <normalized_url>]`.
- **Manual / deploy:** Deno exists in the built image; a Netscape cookies file is mounted at
  `YTDLP_COOKIES_FILE`; one real YouTube and one real Vimeo URL are submitted through the UI.

## Workflow Closure

**No BLOCKING closure test applies.** Each automated behavior is **LEAF**:

- `_normalize_source_url`, `_host_matches`, `_classify_host`, `_host_flags`, and
  `build_crisp_ytdlp_command` are pure same-module helpers. Unit tests fully observe their input ->
  output behavior.
- Environment and filesystem lookup are deliberately outside the builder in
  `_resolve_cookies_file_from_env`, and are tested through `download_crisp_source` with `monkeypatch`
  and `tmp_path`.
- `download_crisp_source` invokes an injected synchronous `runner` in the same module. Unit tests
  observe the command, timeout value, error mapping, and partial-output cleanup.
- The external `yt-dlp`/network path remains outside unit scope and is covered by Docker/runbook
  checks. Cancellation of thread-backed app calls is documented as best-effort; the subprocess
  timeout is the bounded execution guarantee.

The real end-to-end "a YouTube URL produces a reel" is verified manually on the deploy and recorded
in `deploy/RAILWAY-RUNBOOK.md` §7.

---

## Behavior 1: URL normalization and host classification

### Test Specification

**Given** a source URL, **when** normalized and classified, **then** the normalized URL has a usable
scheme+host and classification returns `"youtube"`, `"vimeo"`, or `"generic"` using exact-or-dot
host boundary matching only.

- youtube: `youtube.com/watch?v=...`, `youtu.be/...`, `m.youtube.com/...`,
  `www.youtube.com/...`, `music.youtube.com/...`
- vimeo: `vimeo.com/...`, `player.vimeo.com/...`
- generic: any other schemeful host (`https://cdn.example.com/v.mp4`, Loom, etc.)
- scheme-less YouTube/Vimeo host-like input: normalize to `https://...`
- invalid: empty string, `clip.mp4`, `/tmp/source.mp4`, or a malformed scheme with no host

**Edge Cases:** uppercase host, trailing query+fragment, lookalike domains, and path-only input.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_hooks.py` or `tests/test_ingest.py`.

### 🔴 Red

```python
import pytest
from reel_af.render.hooks import _classify_host, _normalize_source_url

@pytest.mark.parametrize("url,kind", [
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://m.youtube.com/watch?v=abc", "youtube"),
    ("HTTPS://YOUTU.BE/AbC", "youtube"),
    ("https://vimeo.com/123456", "vimeo"),
    ("https://player.vimeo.com/video/123456", "vimeo"),
    ("https://cdn.example.com/clip.mp4", "generic"),
])
def test_classify_host(url, kind):
    assert _classify_host(url) == kind

@pytest.mark.parametrize("url", [
    "https://notyoutube.com/x",
    "https://evil-youtube.com/x",
    "https://youtube.com.evil.test/x",
    "https://notvimeo.com/x",
])
def test_lookalike_domains_are_generic(url):
    assert _classify_host(url) == "generic"

def test_scheme_less_known_hosts_are_normalized():
    assert _normalize_source_url("www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"
    assert _classify_host("youtu.be/abc") == "youtube"

@pytest.mark.parametrize("bad", ["", "clip.mp4", "/tmp/source.mp4", "https:///missing-host"])
def test_source_url_requires_host(bad):
    with pytest.raises(ValueError):
        _classify_host(bad)
```

### 🟢 Green

```python
from urllib.parse import urlparse

YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
VIMEO_HOSTS = ("vimeo.com",)
_SCHEMELESS_HOSTS = YOUTUBE_HOSTS + VIMEO_HOSTS

def _host_matches(host: str, candidates: tuple[str, ...]) -> bool:
    return any(host == candidate or host.endswith("." + candidate) for candidate in candidates)

def _normalize_source_url(source_url: str) -> str:
    raw = str(source_url).strip()
    if not raw:
        raise ValueError("source_url is required")

    parsed = urlparse(raw)
    if parsed.scheme:
        if not parsed.hostname:
            raise ValueError("source_url must include a host")
        return raw

    first_segment = raw.split("/", 1)[0].lower()
    if _host_matches(first_segment, _SCHEMELESS_HOSTS):
        return f"https://{raw}"

    raise ValueError("source_url must include a scheme and host")

def _classify_host(source_url: str) -> str:
    normalized = _normalize_source_url(source_url)
    host = (urlparse(normalized).hostname or "").lower()
    if _host_matches(host, YOUTUBE_HOSTS):
        return "youtube"
    if _host_matches(host, VIMEO_HOSTS):
        return "vimeo"
    return "generic"
```

### 🔵 Refactor

- [ ] Keep exact-or-dot-boundary matching in `_host_matches`; never use bare `host.endswith(candidate)`.
- [ ] Keep one URL parse in classification after normalization.
- [ ] Keep all host tuples named at module scope.

### Success Criteria

**Automated:** Red fails for missing helpers; Green passes
`uv run pytest tests/test_hooks.py -k 'classify or normalize or lookalike'`; `uv run ruff check` clean.

---

## Behavior 2: Host-aware format ladder

### Test Specification

**Given** a URL, **when** building argv, **then** `-f` uses the crisp itag ladder for YouTube and the
portable `height<=1080` ladder for Vimeo/generic hosts.

**Edge Cases:** explicit `format_selector=` still overrides host defaults, including an explicit
empty string; generic host with no video streams is a runtime yt-dlp failure, not a build concern.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_hooks.py` or `tests/test_ingest.py`.

### 🔴 Red

```python
from reel_af.render.hooks import (
    CRISP_YTDLP_FORMAT,
    GENERIC_YTDLP_FORMAT,
    build_crisp_ytdlp_command,
)

def test_youtube_uses_itag_ladder(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    assert cmd[1] == "-f" and cmd[2] == CRISP_YTDLP_FORMAT
    assert "height<=1080" not in " ".join(cmd)

def test_vimeo_uses_generic_ladder(tmp_path):
    cmd = build_crisp_ytdlp_command("https://vimeo.com/123", tmp_path / "s.mp4")
    assert cmd[1] == "-f" and cmd[2] == GENERIC_YTDLP_FORMAT
    assert GENERIC_YTDLP_FORMAT == "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"

def test_explicit_format_selector_overrides_host_even_when_empty(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4", format_selector="")
    assert cmd[2] == ""
```

### 🟢 Green

```python
GENERIC_YTDLP_FORMAT = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
_FORMAT_BY_HOST = {
    "youtube": CRISP_YTDLP_FORMAT,
    "vimeo": GENERIC_YTDLP_FORMAT,
}

def build_crisp_ytdlp_command(
    source_url: str,
    output_path: str | Path,
    *,
    format_selector: str | None = None,
    merge_output_format: str = YTDLP_MERGE_OUTPUT_FORMAT,
    cookies_file: str | Path | None = None,
) -> list[str]:
    """Build the vertical-safe yt-dlp command used by the real-footage path."""

    normalized_url = _normalize_source_url(source_url)
    host_kind = _classify_host(normalized_url)
    selected_format = (
        format_selector
        if format_selector is not None
        else _FORMAT_BY_HOST.get(host_kind, GENERIC_YTDLP_FORMAT)
    )
    target = Path(output_path)
    return [
        "yt-dlp",
        "-f",
        selected_format,
        "--merge-output-format",
        merge_output_format,
        *_host_flags(host_kind, cookies_file),
        "-o",
        str(target),
        normalized_url,
    ]
```

**Export contract:** add `GENERIC_YTDLP_FORMAT`, `YTDLP_COOKIES_FILE_ENV`, and
`YTDLP_DOWNLOAD_TIMEOUT_S` to `__all__` because tests and operator-facing checks import them as
named public constants. Keep `_FORMAT_BY_HOST`, `_host_matches`, `_classify_host`,
`_normalize_source_url`, `_host_flags`, `YTDLP_ERROR_TAIL_CHARS`, and marker constants private.

### 🔵 Refactor

- [ ] Preserve public type annotations on `build_crisp_ytdlp_command`.
- [ ] Select format via `format_selector is not None`, not truthiness.
- [ ] Assemble argv once; do not scatter `cmd += ...` across branches.
- [ ] Keep the existing YouTube back-compat test unchanged and green.

### Success Criteria

**Automated:** new format tests pass;
`tests/test_hooks.py::test_crisp_ytdlp_command_uses_vertical_native_format` still passes unchanged;
`uv run ruff check src/reel_af/render/hooks.py tests/` clean.

---

## Behavior 3: YouTube JS runtime and image support

### Test Specification

**Given** a YouTube URL, **when** building argv, **then** it contains `--js-runtimes deno`; for Vimeo
and generic hosts it does not.

**Edge Cases:** flag lands after the format so `cmd[:2] == ["yt-dlp", "-f"]` stays true; host-specific
flags are assembled by one helper.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_hooks.py` or `tests/test_ingest.py`,
`Dockerfile`.

### 🔴 Red

```python
def test_youtube_adds_deno_runtime(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    i = cmd.index("--js-runtimes")
    assert cmd[i + 1] == "deno"
    assert cmd.index("-f") < i

def test_generic_has_no_js_runtime(tmp_path):
    cmd = build_crisp_ytdlp_command("https://vimeo.com/1", tmp_path / "s.mp4")
    assert "--js-runtimes" not in cmd
```

### 🟢 Green

```python
YOUTUBE_JS_RUNTIME = "deno"

def _host_flags(host_kind: str, cookies_file: str | Path | None) -> list[str]:
    flags: list[str] = []
    if host_kind == "youtube":
        flags.extend(["--js-runtimes", YOUTUBE_JS_RUNTIME])
    if cookies_file is not None:
        flags.extend(["--cookies", str(cookies_file)])
    return flags
```

**Dockerfile integration:** install a pinned Deno version and installer prerequisite. Use a named
build arg/env value, not an unpinned installer:

```dockerfile
ARG DENO_VERSION=2.4.0
ENV DENO_INSTALL=/usr/local

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-montserrat \
        fonts-dejavu-core \
        chromium \
        curl \
        gnupg \
        ca-certificates \
        unzip \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://deno.land/install.sh | sh -s -- v${DENO_VERSION} \
    && deno --version \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
```

### 🔵 Refactor

- [ ] `_host_flags` is part of Green, not a later cleanup.
- [ ] `YOUTUBE_JS_RUNTIME` is named once.
- [ ] Docker build verifies `deno --version`.

### Success Criteria

**Automated:** new flag tests pass; YouTube back-compat test still green.
**Manual:** `docker build` succeeds and `docker run ... deno --version` prints the pinned version.

---

## Behavior 4: Cookies resolved by the wrapper, not the builder

### Test Specification

**Given** explicit `cookies_file=...`, **when** building argv, **then** argv contains
`--cookies <file>`. **Given** `YTDLP_COOKIES_FILE` is set to an existing file, **when**
`download_crisp_source` runs, **then** the captured command includes `--cookies <file>`. **Given** the
env var is unset or empty, no cookies flag is passed. **Given** env is set to a missing path, the
wrapper raises `RuntimeError` before invoking the runner.

**Property:** builder output depends only on its arguments; env/filesystem only affect
`download_crisp_source`.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_hooks.py` or `tests/test_ingest.py`.

### 🔴 Red

```python
from reel_af.render.hooks import YTDLP_COOKIES_FILE_ENV, download_crisp_source

class _Proc:
    def __init__(self, rc=0, err=""):
        self.returncode = rc
        self.stderr = err

def test_builder_adds_explicit_cookies_file(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4", cookies_file=cookies)
    assert cmd[cmd.index("--cookies") + 1] == str(cookies)

def test_download_resolves_configured_cookies_file(tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setenv(YTDLP_COOKIES_FILE_ENV, str(cookies))
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert captured["cmd"][captured["cmd"].index("--cookies") + 1] == str(cookies)

def test_no_cookies_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    download_crisp_source("https://vimeo.com/1", tmp_path / "s.mp4", runner=runner)
    assert "--cookies" not in captured["cmd"]

def test_missing_configured_cookies_file_errors_before_runner(tmp_path, monkeypatch):
    monkeypatch.setenv(YTDLP_COOKIES_FILE_ENV, str(tmp_path / "missing.txt"))
    called = False

    def runner(cmd, **kwargs):
        nonlocal called
        called = True
        return _Proc()

    with pytest.raises(RuntimeError, match=YTDLP_COOKIES_FILE_ENV):
        download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert called is False
```

### 🟢 Green

```python
YTDLP_COOKIES_FILE_ENV = "YTDLP_COOKIES_FILE"

def _resolve_cookies_file_from_env() -> Path | None:
    configured = (os.getenv(YTDLP_COOKIES_FILE_ENV) or "").strip()
    if not configured:
        return None

    cookies_path = Path(configured)
    cookies_exists = cookies_path.is_file()
    if not cookies_exists:
        raise RuntimeError(f"{YTDLP_COOKIES_FILE_ENV} is set but not a file: {configured!r}")
    return cookies_path
```

`download_crisp_source` calls `_resolve_cookies_file_from_env()` and passes the result as
`cookies_file=` into `build_crisp_ytdlp_command`. The builder never reads env and never calls
`Path.is_file()`.

### 🔵 Refactor

- [ ] Env var name is defined once as `YTDLP_COOKIES_FILE_ENV`.
- [ ] Filesystem result is bound before branching (`cookies_exists = cookies_path.is_file()`).
- [ ] Missing configured cookies is a `RuntimeError` at the wrapper boundary and is tested through
  `download_crisp_source`.

### Success Criteria

**Automated:** cookie tests pass; unset-env test proves non-gated URLs still build cookie-free;
builder purity is preserved.
**Manual:** a mounted Netscape-format cookies file at `YTDLP_COOKIES_FILE` lets a gated YouTube URL
get past the cookie gate on deploy.

---

## Behavior 5: Bounded download failures and actionable errors

### Test Specification

**Given** a fake runner, **when** `download_crisp_source` runs, **then** it passes the default timeout
unless a caller overrides it. **Given** non-zero return or timeout, **then** the target file and known
yt-dlp `.part` sibling are deleted. **Given** stderr contains bot/cookie text, **then** the raised
`RuntimeError` names
`YTDLP_COOKIES_FILE`. **Given** stderr contains JS-runtime text, **then** the message mentions deno
and the image requirement. Generic failures preserve the stderr tail without unrelated hints.

**Files touched:** `src/reel_af/render/hooks.py`, `tests/test_hooks.py` or `tests/test_ingest.py`.

### 🔴 Red

```python
import subprocess
from reel_af.render.hooks import YTDLP_DOWNLOAD_TIMEOUT_S

def test_download_uses_default_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    captured = {}

    def runner(cmd, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return _Proc()

    download_crisp_source("https://vimeo.com/1", tmp_path / "s.mp4", runner=runner)
    assert captured["timeout"] == YTDLP_DOWNLOAD_TIMEOUT_S

def test_gated_failure_hints_cookies_and_removes_partial(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    out = tmp_path / "s.mp4"
    part = tmp_path / "s.mp4.part"
    out.write_text("partial")
    part.write_text("partial")

    def runner(cmd, **kwargs):
        return _Proc(1, "ERROR: Sign in to confirm you're not a bot. Use --cookies")

    with pytest.raises(RuntimeError, match=YTDLP_COOKIES_FILE_ENV):
        download_crisp_source("https://youtu.be/x", out, runner=runner)
    assert not out.exists()
    assert not part.exists()

def test_js_runtime_failure_hints_deno_image(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)

    def runner(cmd, **kwargs):
        return _Proc(1, "No supported JavaScript runtime could be found")

    with pytest.raises(RuntimeError) as excinfo:
        download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    message = str(excinfo.value).lower()
    assert "deno" in message
    assert "image" in message

def test_generic_failure_has_no_false_remedy_hint(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)

    def runner(cmd, **kwargs):
        return _Proc(1, "plain extractor failure")

    with pytest.raises(RuntimeError) as excinfo:
        download_crisp_source("https://vimeo.com/1", tmp_path / "s.mp4", runner=runner)
    message = str(excinfo.value)
    assert "plain extractor failure" in message
    assert YTDLP_COOKIES_FILE_ENV not in message
    assert "deno" not in message.lower()

def test_timeout_removes_partial_and_raises_runtime_error(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    out = tmp_path / "s.mp4"
    part = tmp_path / "s.mp4.part"

    def runner(cmd, **kwargs):
        out.write_text("partial")
        part.write_text("partial")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    with pytest.raises(RuntimeError, match="timed out"):
        download_crisp_source("https://vimeo.com/1", out, runner=runner)
    assert not out.exists()
    assert not part.exists()
```

### 🟢 Green

```python
YTDLP_DOWNLOAD_TIMEOUT_S = 600.0
YTDLP_ERROR_TAIL_CHARS = 1200
_BOT_MARKERS = ("sign in to confirm", "not a bot", "--cookies")
_JS_RUNTIME_MARKERS = ("no supported javascript runtime", "--js-runtimes")

def _download_failure_hint(stderr: str) -> str:
    lower = stderr.lower()
    hints: list[str] = []
    if any(marker in lower for marker in _BOT_MARKERS):
        hints.append(
            f"Set {YTDLP_COOKIES_FILE_ENV} to a valid Netscape-format cookies export."
        )
    if any(marker in lower for marker in _JS_RUNTIME_MARKERS):
        hints.append("Install deno in the image and keep --js-runtimes deno enabled.")
    if not hints:
        return ""
    return " " + " ".join(hints)

def _remove_partial_outputs(target: Path) -> None:
    for candidate in (target, target.with_name(target.name + ".part")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass

def download_crisp_source(
    source_url: str,
    output_path: str | Path,
    *,
    timeout_s: float | None = YTDLP_DOWNLOAD_TIMEOUT_S,
    runner: Any = subprocess.run,
) -> Path:
    """Download a source video with the crisp vertical-safe selector."""

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cookies_file = _resolve_cookies_file_from_env()
    cmd = build_crisp_ytdlp_command(source_url, target, cookies_file=cookies_file)
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _remove_partial_outputs(target)
        timeout_label = "the configured timeout" if timeout_s is None else f"{timeout_s:g}s"
        raise RuntimeError(f"yt-dlp crisp download timed out after {timeout_label}") from exc

    if getattr(proc, "returncode", 0) != 0:
        _remove_partial_outputs(target)
        stderr = str(getattr(proc, "stderr", ""))
        tail = stderr[-YTDLP_ERROR_TAIL_CHARS:]
        hint = _download_failure_hint(stderr)
        raise RuntimeError(
            "yt-dlp crisp download failed "
            f"(exit {getattr(proc, 'returncode', 'unknown')}): {tail}{hint}"
        )
    return target
```

### 🔵 Refactor

- [ ] Keep marker lists named and private.
- [ ] Keep `YTDLP_ERROR_TAIL_CHARS` and `YTDLP_DOWNLOAD_TIMEOUT_S` named.
- [ ] Keep cleanup in one helper and call it on non-zero return and timeout.
- [ ] Delete both the final target and the known yt-dlp `.part` sibling.
- [ ] Do not promise async cancellation kills a thread-backed subprocess; document timeout as the
  hard execution bound.

### Success Criteria

**Automated:** timeout, cleanup, bot hint, JS-runtime hint, generic failure, and success-path tests
pass; existing `download_crisp_source` success behavior still returns the target `Path`.
**Manual:** deploy logs for a gated failure include either the cookie hint, the deno/image hint, or
both, depending on stderr.

---

## Behavior 6: App/API surface, Docker wiring, and runbook

### Test Specification

**Given** `download_crisp_source` raises an ingest/config `RuntimeError`, **when** the app reasoner
worker path runs, **then** `_run_composite_reels` returns `{"error": "<message>"}` instead of letting
the exception escape as an opaque execution failure. CLI can keep propagating the same exception text.

**Files touched:** `src/reel_af/app.py`, `Dockerfile`, `deploy/RAILWAY-RUNBOOK.md`,
`tests/test_hooks.py` or focused app test file.

### 🔴 Red

```python
def test_app_video_ingest_error_returns_error(monkeypatch, tmp_path):
    from reel_af import app as app_module

    def fail_download(source_url, output_path):
        raise RuntimeError("YTDLP_COOKIES_FILE is set but not a file: '/missing/cookies.txt'")

    monkeypatch.setattr("reel_af.render.hooks.download_crisp_source", fail_download)
    result = app_module._run_composite_reels(
        url="https://youtu.be/x",
        preset_name="middle-third-dynamic",
        count=1,
        out_path=tmp_path,
        chrome=None,
    )
    assert "error" in result
    assert "YTDLP_COOKIES_FILE" in result["error"]
```

### 🟢 Green

```python
try:
    src = download_crisp_source(url, out_path / "source.mp4")
except (RuntimeError, ValueError) as exc:
    return {"error": str(exc)}
```

Update `deploy/RAILWAY-RUNBOOK.md` §7 with the operator contract:

- Cookie file must be a `yt-dlp`/browser-exported Netscape cookies.txt file.
- Railway mount path: `/app/secrets/cookies.txt`.
- Runtime variable: `YTDLP_COOKIES_FILE=/app/secrets/cookies.txt`.
- Validation command inside the image/container:
  `test -f "$YTDLP_COOKIES_FILE" && deno --version`.
- Missing file behavior: app result contains an error naming `YTDLP_COOKIES_FILE`.
- Expired/invalid cookies behavior: yt-dlp failure contains the stderr tail plus the cookie hint.
- JS runtime behavior: if Deno is missing, yt-dlp failure contains the stderr tail plus the
  deno/image hint.

### Success Criteria

**Automated:**

- [x] `uv run pytest tests/test_hooks.py` green.
- [x] App error-boundary test green.
- [x] `uv run pytest tests/test_reels_cli.py tests/test_composite_pipeline.py` green.
- [x] `uv run ruff check` clean on changed lines (`hooks.py`/`app.py` additions +
  `tests/test_ingest.py`); two pre-existing F401/E errors on HEAD lines are out of scope.
- [x] No duplicate env string, stderr tail integer, or host flag assembly blocks in `hooks.py`.

**Manual (deploy):**

- [ ] `Dockerfile` builds with pinned Deno and `deno --version` succeeds in the image.
- [ ] `YTDLP_COOKIES_FILE=/app/secrets/cookies.txt` points at an existing mounted cookies export.
- [ ] A real YouTube URL submitted via the UI reaches `succeeded` or fails with an actionable
  cookies/deno hint that matches the runbook.
- [ ] A real Vimeo URL downloads with no cookies configured.
- [ ] Runbook §7 updated from "known limitation" to concrete setup + troubleshooting steps.

---

## Integration & E2E Testing

- **Integration (deploy):** rebuild `reel-af` image, verify `deno --version`, set
  `YTDLP_COOKIES_FILE`, POST one YouTube and one Vimeo URL through the public UI, and poll to
  `succeeded` or a documented hinted failure.
- **No CI network test:** real downloads depend on host cookies and anti-bot controls. The argv,
  env, timeout, cleanup, and API failure contracts are covered by unit tests; the network leg is a
  manual deploy check.

## Order of Implementation

1. URL normalization and exact-boundary host classification.
2. Host-aware format ladder with typed builder signature and `GENERIC_YTDLP_FORMAT` export.
3. `_host_flags` + YouTube Deno flag + Dockerfile Deno installation.
4. Cookie env resolution in `download_crisp_source`; builder receives explicit `cookies_file`.
5. Default timeout, partial-output cleanup, bot/deno error hints.
6. App error translation, runbook details, full regression gates.

## Review Resolution Checklist

- [x] Unsafe `host.endswith(candidate)` matching removed from the plan; dot-boundary tests added.
- [x] Lookalike domain negative tests added.
- [x] Scheme-less URL behavior specified and tested.
- [x] Builder purity preserved by passing explicit `cookies_file`.
- [x] Missing cookies config behavior specified as wrapper-level `RuntimeError`.
- [x] JS-runtime failure hint test added.
- [x] Public function type signatures preserved in snippets.
- [x] `format_selector is not None` used for override semantics.
- [x] `GENERIC_YTDLP_FORMAT`, `YTDLP_COOKIES_FILE_ENV`, and `YTDLP_DOWNLOAD_TIMEOUT_S` exports
  required.
- [x] Default yt-dlp timeout and best-effort cancellation policy specified.
- [x] Target plus `.part` cleanup specified for non-zero return and timeout.
- [x] Dockerfile Deno install pinned and includes installer prerequisite + verification.
- [x] Runbook cookies/deno provisioning details specified.
- [x] App/API failure semantics specified and covered by a test.
- [x] CodeCleanup constants and single flag-assembly helper specified.

## References

- Review addressed:
  `thoughts/searchable/shared/plans/2026-07-10-tdd-video-ingest-youtube-vimeo-REVIEW.md`
- Target: `src/reel_af/render/hooks.py:17,54,79`
- Back-compat anchor test: `tests/test_hooks.py:26`
- Call sites: `src/reel_af/app.py:635`, `src/reel_af/render/composite_pipeline.py:99`,
  `src/reel_af/cli.py:378`
- Deploy context / follow-up: `deploy/RAILWAY-RUNBOOK.md` §7; bead `A1_workspace-blueprint-gm9`
- Memory: `youtube-download-ejs-fix` (deno + cookies), `reel-af-railway-deploy`
