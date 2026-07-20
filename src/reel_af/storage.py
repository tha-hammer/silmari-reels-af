"""Deliver produced reels out to the shared object store (T10).

Symmetric to the reel-af-ui upload store (T7): the composite pipeline writes a reel
to the node's *ephemeral* filesystem, so nothing the browser can reach — this uploads
that reel to the shared S3-compatible bucket and presigns a GET url for download.

Fail-soft by design: with no ``REEL_BUCKET_*`` configured (or a missing file), it
returns ``None`` and the composite result simply omits ``download_url`` rather than
failing the whole run. The client (``client_factory``) is injectable for tests so no
boto3/network is needed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_DEFAULT_DELIVERY_TTL_S = 86400  # 24h — a rendered reel stays downloadable for a while
_CORE_A1_ARTIFACTS = (
    ("composite_ref", "composite.ts.md"),
    ("words_ref", "transcript.words.json"),
    ("hook_ref", "hook-plan.json"),
)
_A1_SIDECAR_REF_KEYS = frozenset(
    {
        "mined_candidates_ref",
        "accepted_candidates_ref",
        "strategy_ref",
        "blueprint_ref",
        "script_coherence_ref",
    }
)


def _bucket() -> str | None:
    return os.getenv("REEL_BUCKET_NAME") or None


def _delivery_ttl_s() -> int:
    return int(os.getenv("REEL_DELIVERY_TTL_S", str(_DEFAULT_DELIVERY_TTL_S)))


def _artifact_ttl_s() -> int:
    return int(os.getenv("REEL_ARTIFACT_TTL_S") or _delivery_ttl_s())


def _client(client_factory=None):
    if client_factory is not None:
        return client_factory()
    import boto3  # lazy: only imported when a delivery actually runs

    return boto3.client(
        "s3",
        endpoint_url=os.getenv("REEL_BUCKET_ENDPOINT") or None,
        aws_access_key_id=os.getenv("REEL_BUCKET_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.getenv("REEL_BUCKET_SECRET_ACCESS_KEY") or None,
        region_name=os.getenv("REEL_BUCKET_REGION", "auto"),
    )


def upload_reel(
    local_path,
    *,
    run_id: str,
    filename: str | None = None,
    client_factory=None,
    ttl_s: int | None = None,
) -> str | None:
    """Upload a produced reel to the shared bucket and return a presigned GET url.

    ``filename`` overrides the delivered object-key basename (and thus the browser's
    download name, since the presign carries no Content-Disposition); its own basename
    is taken (``Path(filename).name``) so it can never escape the ``outputs/{run_id}/``
    prefix. ``None`` (default) keeps the local ``path.name``.

    Returns ``None`` (fail-soft) when the bucket is unconfigured or the file is
    missing, so the caller can still surface the local ``video_path``.
    """
    bucket = _bucket()
    path = Path(local_path)
    if not bucket or not path.is_file():
        return None
    basename = Path(filename).name if filename else path.name
    key = f"outputs/{run_id}/{basename}"
    client = _client(client_factory)
    client.upload_file(str(path), bucket, key)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_s if ttl_s is not None else _delivery_ttl_s(),
    )


def _hosted_http_url(ref: str, *, field: str) -> str:
    parsed = urlparse(ref) if isinstance(ref, str) else urlparse("")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"A1 artifact {field} presigned URL is not browser-deliverable")
    return ref


def _read_core_artifact(ref: Any, *, field: str) -> bytes:
    if not isinstance(ref, str) or not ref:
        raise ValueError(f"A1 core artifact {field} is required")
    try:
        return Path(ref).read_bytes()
    except OSError as exc:
        raise OSError(f"A1 core artifact {field} is unavailable") from exc


def _parse_hook_plan(body: bytes) -> dict:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("A1 hook_ref JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise ValueError("A1 hook_ref JSON must be an object")
    return parsed


def _contains_local_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_local_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_local_path(item) for item in value)
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme:
        return parsed.scheme == "file"
    return Path(value).is_absolute()


def _hook_body_with_published_composite(
    hook_plan: dict,
    *,
    composite_url: str,
    raw_core_refs: Mapping[str, Any],
) -> bytes:
    for clip in hook_plan.get("clips", []):
        if isinstance(clip, dict) and "composite_ref" in clip:
            clip["composite_ref"] = composite_url
    if _contains_local_path(hook_plan):
        raise ValueError("A1 hook_ref still contains a local artifact path")
    body = json.dumps(hook_plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    for raw_ref in raw_core_refs.values():
        if isinstance(raw_ref, str) and raw_ref and raw_ref.encode("utf-8") in body:
            raise ValueError("A1 hook_ref still contains a local core artifact ref")
    return body


def _put_and_presign_artifact(
    client,
    *,
    bucket: str,
    key: str,
    body: bytes,
    ttl_s: int,
    field: str,
) -> str:
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body)
    except Exception as exc:  # noqa: BLE001 - storage adapters vary by backend
        raise OSError(f"A1 artifact {field} upload failed: {exc}") from exc
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_s,
        )
    except Exception as exc:  # noqa: BLE001 - boto3-compatible clients vary
        raise OSError(f"A1 artifact {field} presign failed: {exc}") from exc
    return _hosted_http_url(url, field=field)


def publish_a1_artifacts(
    result: Mapping[str, Any],
    *,
    run_id: str,
    client_factory=None,
    ttl_s: int | None = None,
    prefix: str = "plans",
) -> dict:
    """Publish a transcript-to-plan A1 artifact triple to object storage.

    With no bucket configured this is a no-op copy for co-located local dev. With a
    bucket configured, the core triple is strict: all three local artifacts must be
    readable, uploaded, presigned as hosted HTTP(S) URLs, or the function raises.
    """
    published = dict(result)
    bucket = _bucket()
    if not bucket:
        return published

    raw_core_refs = {field: published.get(field) for field, _filename in _CORE_A1_ARTIFACTS}
    bodies = {
        field: _read_core_artifact(raw_core_refs[field], field=field)
        for field, _filename in _CORE_A1_ARTIFACTS
    }
    hook_plan = _parse_hook_plan(bodies["hook_ref"])
    ttl = ttl_s if ttl_s is not None else _artifact_ttl_s()
    client = _client(client_factory)

    composite_key = f"{prefix}/{run_id}/composite.ts.md"
    composite_url = _put_and_presign_artifact(
        client,
        bucket=bucket,
        key=composite_key,
        body=bodies["composite_ref"],
        ttl_s=ttl,
        field="composite_ref",
    )
    words_key = f"{prefix}/{run_id}/transcript.words.json"
    words_url = _put_and_presign_artifact(
        client,
        bucket=bucket,
        key=words_key,
        body=bodies["words_ref"],
        ttl_s=ttl,
        field="words_ref",
    )
    hook_body = _hook_body_with_published_composite(
        hook_plan,
        composite_url=composite_url,
        raw_core_refs=raw_core_refs,
    )
    hook_key = f"{prefix}/{run_id}/hook-plan.json"
    hook_url = _put_and_presign_artifact(
        client,
        bucket=bucket,
        key=hook_key,
        body=hook_body,
        ttl_s=ttl,
        field="hook_ref",
    )

    published.update(
        {
            "composite_ref": composite_url,
            "words_ref": words_url,
            "hook_ref": hook_url,
        }
    )
    for key in _A1_SIDECAR_REF_KEYS:
        published.pop(key, None)
    return published


__all__ = ["upload_reel", "publish_a1_artifacts"]
