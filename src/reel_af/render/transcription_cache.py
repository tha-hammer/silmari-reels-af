"""Transcription/word-timings cache keyed by source content (AF-4pz.1).

Whisper is the composite pipeline's most expensive step; producing multiple
reels from one source re-transcribed every run. Derived word timings are
cached in the shared bucket (the node already holds ``REEL_BUCKET_*`` creds —
T7/T10) under ``cache/transcription/<sha256(bytes)>/<model>.words.json``.
Content-checksum keying gives invalidation for free: a changed source misses
cleanly. The model is the one parameter that changes the words, so it is part
of the key.

FAIL-SOFT is the contract: no bucket configured, transport errors, or corrupt
payloads are treated as a miss (get) or a no-op (put) — the cache can never
fail a reel.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from reel_af.render.captions import WHISPER_MODEL

_HASH_CHUNK_BYTES = 1 << 20
CACHE_PREFIX = "cache/transcription"

WordTimings = "list[tuple[float, float, str]]"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def transcription_cache_key(src: Path, *, model: str = WHISPER_MODEL) -> str:
    """Stable cache key: source content checksum + whisper model."""
    return f"{CACHE_PREFIX}/{_sha256_file(Path(src))}/{model}.words.json"


class BucketTranscriptionCache:
    """S3-backed word-timings store. Every failure path is a miss/no-op."""

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory

    def _bucket_and_client(self):
        from reel_af.storage import _bucket, _client

        bucket = _bucket()
        if not bucket:
            return None, None
        return bucket, _client(self._client_factory)

    def get(self, key: str) -> list | None:
        try:
            bucket, client = self._bucket_and_client()
            if bucket is None:
                return None
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            words = json.loads(body)
        except Exception:  # noqa: BLE001 - fail-soft: any trouble is a miss
            return None
        if not isinstance(words, list) or not all(
            isinstance(word, (list, tuple)) and len(word) == 3 for word in words
        ):
            return None
        return words

    def put(self, key: str, words: Sequence[Sequence[Any]]) -> None:
        try:
            bucket, client = self._bucket_and_client()
            if bucket is None:
                return
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps([list(word) for word in words]).encode(),
                ContentType="application/json",
            )
        except Exception:  # noqa: BLE001 - fail-soft: best-effort store only
            return


def cached_words(
    src: Path,
    *,
    transcribe: Callable[..., Any],
    cache: Any | None,
    workdir: Path | None = None,
    model: str = WHISPER_MODEL,
) -> list[tuple[float, float, str]]:
    """Word timings for ``src``, via the cache when one is wired.

    ``cache=None`` is a pure passthrough (existing callers/tests unchanged).
    A hit skips the whisper subprocess entirely; a miss transcribes and stores
    best-effort. Cache errors never propagate (fail-soft stores + the guard
    here around key computation).
    """
    if cache is None:
        return transcribe(src, workdir=workdir)

    try:
        key = transcription_cache_key(src, model=model)
        hit = cache.get(key)
    except Exception:  # noqa: BLE001 - fail-soft: cache trouble is a miss
        key, hit = None, None
    if hit is not None:
        return [(float(start), float(end), str(text)) for start, end, text in hit]

    words = transcribe(src, workdir=workdir)
    if key is not None:
        try:
            cache.put(key, [list(word) for word in words])
        except Exception:  # noqa: BLE001 - best-effort store only
            pass
    return words


__all__ = [
    "CACHE_PREFIX",
    "BucketTranscriptionCache",
    "cached_words",
    "transcription_cache_key",
]
