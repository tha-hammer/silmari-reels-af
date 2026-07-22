"""AF-4pz.1 — transcription/word-timings cache keyed by source content.

Producing a 2nd+ reel from the same source must not re-run whisper: derived
word timings are cached under ``cache/transcription/<sha256>/<model>.words.json``
(content-checksum keying ⇒ a changed source misses cleanly) and the cache is
FAIL-SOFT — it can never fail a reel.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from reel_af import app
from reel_af.render.captions import WHISPER_MODEL
from reel_af.render.transcription_cache import (
    BucketTranscriptionCache,
    cached_words,
    transcription_cache_key,
)

WORDS = [(0.0, 0.4, "alpha"), (0.5, 0.9, "beta")]


class _CountingTranscribe:
    def __init__(self, words=WORDS):
        self.calls = 0
        self._words = words

    def __call__(self, src, workdir=None, **kw):
        self.calls += 1
        return list(self._words)


class _DictCache:
    def __init__(self, *, get_error=False, put_error=False):
        self.store: dict = {}
        self._get_error = get_error
        self._put_error = put_error

    def get(self, key):
        if self._get_error:
            raise RuntimeError("cache transport down")
        return self.store.get(key)

    def put(self, key, words):
        if self._put_error:
            raise RuntimeError("cache transport down")
        self.store[key] = words


def _src(tmp_path: Path, content: bytes = b"video-bytes", name="src.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


# ── Behavior 1: no cache ⇒ passthrough unchanged ─────────────────────


def test_no_cache_transcribes_every_call(tmp_path):
    transcribe = _CountingTranscribe()
    src = _src(tmp_path)

    first = cached_words(src, transcribe=transcribe, cache=None)
    second = cached_words(src, transcribe=transcribe, cache=None)

    assert first == WORDS and second == WORDS
    assert transcribe.calls == 2


# ── Behaviors 2-3: miss→put→hit + checksum invalidation ──────────────


def test_second_call_hits_cache_and_skips_transcribe(tmp_path):
    transcribe = _CountingTranscribe()
    cache = _DictCache()
    src = _src(tmp_path)

    first = cached_words(src, transcribe=transcribe, cache=cache)
    second = cached_words(src, transcribe=transcribe, cache=cache)

    assert transcribe.calls == 1                      # whisper ran once
    assert first == WORDS
    assert second == WORDS                            # hit normalizes to tuples
    assert all(isinstance(w, tuple) for w in second)


def test_changed_source_bytes_miss_cleanly(tmp_path):
    transcribe = _CountingTranscribe()
    cache = _DictCache()

    cached_words(_src(tmp_path, b"v1", "a.mp4"), transcribe=transcribe, cache=cache)
    cached_words(_src(tmp_path, b"v2", "b.mp4"), transcribe=transcribe, cache=cache)

    assert transcribe.calls == 2                      # different checksum ⇒ miss
    assert len(cache.store) == 2


def test_key_is_content_checksum_plus_model(tmp_path):
    src = _src(tmp_path, b"stable-bytes")
    digest = hashlib.sha256(b"stable-bytes").hexdigest()
    assert transcription_cache_key(src, model=WHISPER_MODEL) == (
        f"cache/transcription/{digest}/{WHISPER_MODEL}.words.json"
    )


# ── Behavior 4: fail-soft — cache errors never fail the reel ─────────


def test_cache_errors_fall_through_to_transcribe(tmp_path):
    transcribe = _CountingTranscribe()
    src = _src(tmp_path)

    words = cached_words(
        src, transcribe=transcribe, cache=_DictCache(get_error=True, put_error=True)
    )

    assert words == WORDS
    assert transcribe.calls == 1


# ── Behavior 5: the bucket-backed store ──────────────────────────────


class _FakeS3:
    def __init__(self):
        self.objects: dict = {}
        self.put_calls: list = []

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 casing
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):  # noqa: N803
        self.put_calls.append((Bucket, Key))
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()


def test_bucket_cache_disabled_without_bucket_env(monkeypatch, tmp_path):
    monkeypatch.delenv("REEL_BUCKET_NAME", raising=False)
    cache = BucketTranscriptionCache(client_factory=lambda: (_ for _ in ()).throw(AssertionError("no client without a bucket")))
    assert cache.get("cache/transcription/x/y.words.json") is None
    cache.put("cache/transcription/x/y.words.json", [[0.0, 0.1, "a"]])   # no-op, no raise


def test_bucket_cache_round_trip_and_corrupt_payload(monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", "test-bucket")
    s3 = _FakeS3()
    cache = BucketTranscriptionCache(client_factory=lambda: s3)
    key = "cache/transcription/abc/model.words.json"

    assert cache.get(key) is None                    # cold miss
    cache.put(key, [[0.0, 0.4, "alpha"]])
    assert s3.put_calls == [("test-bucket", key)]
    assert cache.get(key) == [[0.0, 0.4, "alpha"]]

    s3.objects[key] = b"{not json"
    assert cache.get(key) is None                    # corrupt ⇒ miss
    s3.objects[key] = json.dumps({"nope": 1}).encode()
    assert cache.get(key) is None                    # wrong shape ⇒ miss


# ── Behavior 6 (closure): 2nd composite run skips the whisper seam ───


def _composite_deps(transcribe, cache):
    def download(url, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"same-source-bytes")
        return dest

    return SimpleNamespace(
        download=download,
        has_audio=lambda src: True,
        transcribe=transcribe,
        probe_duration=lambda src: 180.0,
        transcription_cache=cache,
    )


def test_second_composite_run_skips_whisper(tmp_path):
    words = [(float(i), float(i) + 0.4, f"w{i}") for i in range(180)]
    transcribe = _CountingTranscribe(words=words)
    cache = _DictCache()
    runner = lambda cmd, **kw: None  # noqa: E731 - render subprocess stub

    for run in ("run1", "run2"):
        result = app._run_composite_reels(
            url="https://example.com/v.mp4",
            preset_name="middle-third-dynamic",
            count=1,
            out_path=tmp_path / run,
            chrome=None,
            deps=_composite_deps(transcribe, cache),
            runner=runner,
        )
        assert "error" not in result, result

    assert transcribe.calls == 1     # bead acceptance: 2nd run skips whisper
