"""Video-ingest hardening: URL normalization, host classification, host-aware
yt-dlp argv, cookie resolution, bounded failures, and app error translation.

The pure helpers (`_normalize_source_url`, `_classify_host`, `_host_matches`,
`build_crisp_ytdlp_command`) are tested with no network, env, or filesystem
dependency except ``tmp_path`` for output paths. ``download_crisp_source`` is
tested with injected fake runners and ``monkeypatch``ed env.
"""

from __future__ import annotations

import subprocess

import pytest

from reel_af.render.hooks import (
    CRISP_YTDLP_FORMAT,
    DOWNLOAD_MAX_ATTEMPTS,
    GENERIC_YTDLP_FORMAT,
    YTDLP_COOKIES_B64_ENV,
    YTDLP_COOKIES_FILE_ENV,
    YTDLP_DOWNLOAD_TIMEOUT_S,
    YTDLP_PROXY_ENV,
    _classify_host,
    _is_direct_media_url,
    _normalize_source_url,
    _resolve_cookies_file_from_env,
    build_crisp_ytdlp_command,
    download_crisp_source,
    download_direct_source,
    download_source,
)

# ── Behavior 1: URL normalization and host classification ──────────────────


@pytest.mark.parametrize(
    "url,kind",
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://m.youtube.com/watch?v=abc", "youtube"),
        ("HTTPS://YOUTU.BE/AbC", "youtube"),
        ("https://vimeo.com/123456", "vimeo"),
        ("https://player.vimeo.com/video/123456", "vimeo"),
        ("https://cdn.example.com/clip.mp4", "generic"),
    ],
)
def test_classify_host(url, kind):
    assert _classify_host(url) == kind


@pytest.mark.parametrize(
    "url",
    [
        "https://notyoutube.com/x",
        "https://evil-youtube.com/x",
        "https://youtube.com.evil.test/x",
        "https://notvimeo.com/x",
    ],
)
def test_lookalike_domains_are_generic(url):
    assert _classify_host(url) == "generic"


def test_scheme_less_known_hosts_are_normalized():
    assert (
        _normalize_source_url("www.youtube.com/watch?v=abc")
        == "https://www.youtube.com/watch?v=abc"
    )
    assert _classify_host("youtu.be/abc") == "youtube"


@pytest.mark.parametrize("bad", ["", "clip.mp4", "/tmp/source.mp4", "https:///missing-host"])
def test_source_url_requires_host(bad):
    with pytest.raises(ValueError):
        _classify_host(bad)


# ── Behavior 2: Host-aware format ladder ───────────────────────────────────


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


# ── Behavior 3: YouTube JS runtime and image support ───────────────────────


def test_youtube_adds_deno_runtime(tmp_path):
    cmd = build_crisp_ytdlp_command("https://youtu.be/x", tmp_path / "s.mp4")
    i = cmd.index("--js-runtimes")
    assert cmd[i + 1] == "deno"
    assert cmd.index("-f") < i


def test_generic_has_no_js_runtime(tmp_path):
    cmd = build_crisp_ytdlp_command("https://vimeo.com/1", tmp_path / "s.mp4")
    assert "--js-runtimes" not in cmd


# ── Behavior 4: Cookies resolved by the wrapper, not the builder ───────────


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


# ── Behavior 5: Bounded download failures and actionable errors ────────────


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


# ── Behavior 5b: transient datacenter-IP 403s are retried; hard failures are not ──


def test_transient_403_is_retried_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.setattr("reel_af.render.hooks.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def runner(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:  # flaky googlevideo 403 on the first two attempts
            return _Proc(1, "ERROR: unable to download video data: HTTP Error 403: Forbidden")
        return _Proc(0)

    out = download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert out == tmp_path / "s.mp4"
    assert calls["n"] == 3


def test_persistent_403_exhausts_attempts_then_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.setattr("reel_af.render.hooks.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def runner(cmd, **kwargs):
        calls["n"] += 1
        return _Proc(1, "ERROR: unable to download video data: HTTP Error 403: Forbidden")

    with pytest.raises(RuntimeError, match="403"):
        download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert calls["n"] == DOWNLOAD_MAX_ATTEMPTS


def test_bot_check_failure_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.setattr("reel_af.render.hooks.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def runner(cmd, **kwargs):
        calls["n"] += 1
        return _Proc(1, "ERROR: Sign in to confirm you're not a bot. Use --cookies")

    with pytest.raises(RuntimeError, match=YTDLP_COOKIES_FILE_ENV):
        download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert calls["n"] == 1  # not transient → single attempt, no wasted retries


# ── Path C: BrightData/residential proxy via YTDLP_PROXY_URL ──────────────────


def test_proxy_flag_added_when_env_set(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.setenv(YTDLP_PROXY_ENV, "http://u:p@brd.superproxy.io:33335")
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    cmd = captured["cmd"]
    assert "--proxy" in cmd
    assert cmd[cmd.index("--proxy") + 1] == "http://u:p@brd.superproxy.io:33335"


def test_no_proxy_flag_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.delenv(YTDLP_PROXY_ENV, raising=False)
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert "--proxy" not in captured["cmd"]


# ── Path B: cookies materialized from a base64 secret (YTDLP_COOKIES_B64) ──────


def test_cookies_materialized_from_b64_secret(tmp_path, monkeypatch):
    import base64

    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    raw = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"
    monkeypatch.setenv(YTDLP_COOKIES_B64_ENV, base64.b64encode(raw).decode())

    path = _resolve_cookies_file_from_env()
    assert path is not None
    assert path.read_bytes() == raw  # decoded verbatim


def test_cookies_file_env_takes_precedence_over_b64(tmp_path, monkeypatch):
    import base64

    real = tmp_path / "cookies.txt"
    real.write_text("from-file")
    monkeypatch.setenv(YTDLP_COOKIES_FILE_ENV, str(real))
    monkeypatch.setenv(YTDLP_COOKIES_B64_ENV, base64.b64encode(b"from-b64").decode())

    assert _resolve_cookies_file_from_env() == real


def test_invalid_b64_cookies_raises(monkeypatch):
    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.setenv(YTDLP_COOKIES_B64_ENV, "!!!not base64!!!")
    with pytest.raises(RuntimeError, match=YTDLP_COOKIES_B64_ENV):
        _resolve_cookies_file_from_env()


def test_b64_cookies_reach_the_command(tmp_path, monkeypatch):
    import base64

    monkeypatch.delenv(YTDLP_COOKIES_FILE_ENV, raising=False)
    monkeypatch.setenv(YTDLP_COOKIES_B64_ENV, base64.b64encode(b"cookie").decode())
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    download_crisp_source("https://youtu.be/x", tmp_path / "s.mp4", runner=runner)
    assert "--cookies" in captured["cmd"]


# ── Path A: a pre-fetched direct media URL is streamed, not run through yt-dlp ──


@pytest.mark.parametrize(
    "url,direct",
    [
        ("https://bucket.example/a1/clip.mp4?sig=xyz", True),
        ("https://cdn.example.com/source.mov", True),
        ("https://cdn.example.com/source.webm", True),
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://youtu.be/abc", False),
        ("https://vimeo.com/123", False),
        ("https://cdn.example.com/page", False),  # generic host, but not a media file
    ],
)
def test_is_direct_media_url(url, direct):
    assert _is_direct_media_url(url) is direct


def test_download_direct_source_streams_bytes(tmp_path):
    import io

    def opener(url, timeout=None):
        return io.BytesIO(b"VIDEO-BYTES")

    out = download_direct_source("https://bucket.example/clip.mp4", tmp_path / "s.mp4", opener=opener)
    assert out.read_bytes() == b"VIDEO-BYTES"


def test_download_source_routes_direct_url_to_http(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "reel_af.render.hooks.download_direct_source",
        lambda u, o, **k: calls.append(("direct", u)) or o,
    )
    monkeypatch.setattr(
        "reel_af.render.hooks.download_crisp_source",
        lambda u, o, **k: calls.append(("crisp", u)) or o,
    )
    download_source("https://bucket.example/clip.mp4?sig=x", tmp_path / "s.mp4")
    assert calls == [("direct", "https://bucket.example/clip.mp4?sig=x")]


def test_download_source_routes_youtube_to_ytdlp(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "reel_af.render.hooks.download_direct_source",
        lambda u, o, **k: calls.append(("direct", u)) or o,
    )
    monkeypatch.setattr(
        "reel_af.render.hooks.download_crisp_source",
        lambda u, o, **k: calls.append(("crisp", u)) or o,
    )
    download_source("https://youtu.be/abc", tmp_path / "s.mp4")
    assert calls == [("crisp", "https://youtu.be/abc")]


# ── Behavior 6: App/API surface translates ingest errors to {"error": ...} ──


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


def test_app_lower_third_video_preset_is_wired(monkeypatch, tmp_path):
    from reel_af import app as app_module
    from reel_af.render import lower_third

    src = tmp_path / "source.mp4"
    src.write_bytes(b"\x00")
    calls: dict[str, object] = {}

    monkeypatch.setattr("reel_af.render.hooks.download_crisp_source", lambda url, out: src)
    monkeypatch.setattr("reel_af.render.captions.has_audio_stream", lambda *a, **k: True)  # T8 B2: audio present
    monkeypatch.setattr(
        "reel_af.render.captions.caption_words",
        lambda source, workdir: [
            (0.0, 0.5, "Railway"),
            (0.5, 1.0, "lower"),
            (1.0, 1.5, "third"),
            (1.5, 2.0, "works"),
        ],
    )

    class _FFProbe:
        stdout = "360.0\n"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: _FFProbe())

    def render_lower_third(title, out_seq_dir, **kwargs):
        calls["title"] = title
        return out_seq_dir

    def composite_window(source, t0, dur_s, seq_dir, out, **kwargs):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        calls["composite"] = (source, t0, dur_s, seq_dir, out, kwargs)
        return out

    monkeypatch.setattr(lower_third, "render_lower_third", render_lower_third)
    monkeypatch.setattr(lower_third, "composite_window", composite_window)

    result = app_module._run_composite_reels(
        url="https://youtu.be/x",
        preset_name="horizontal-youtube-lowerthird",
        count=1,
        out_path=tmp_path,
        chrome=None,
    )

    assert "error" not in result
    assert result["reel_count"] == 1
    assert calls["title"] == "Railway lower third works"
    assert result["reels"] == [str(tmp_path / "reel01" / "reel01.mp4")]


# T8 B1b: a source with no audio stream returns a clear error and skips transcription.
def test_composite_no_audio_source_returns_clear_error(tmp_path, monkeypatch):
    from reel_af import app as app_module

    monkeypatch.setattr("reel_af.render.hooks.download_crisp_source", lambda url, out, **k: out)
    called = {"caption": False}

    def _caption(*_a, **_k):
        called["caption"] = True
        return []

    monkeypatch.setattr("reel_af.render.captions.caption_words", _caption)
    monkeypatch.setattr("reel_af.render.captions.has_audio_stream", lambda *a, **k: False)

    result = app_module._run_composite_reels(
        url="https://bucket/x.mp4",
        preset_name="middle-third-dynamic",
        count=1,
        out_path=tmp_path,
        chrome=None,
    )

    assert "no audio track" in result["error"]
    assert result["code"] == "source_no_audio_track"
    assert called["caption"] is False  # transcription short-circuited before caption_words
