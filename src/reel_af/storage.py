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

import os
from pathlib import Path

_DEFAULT_DELIVERY_TTL_S = 86400  # 24h — a rendered reel stays downloadable for a while


def _bucket() -> str | None:
    return os.getenv("REEL_BUCKET_NAME") or None


def _delivery_ttl_s() -> int:
    return int(os.getenv("REEL_DELIVERY_TTL_S", str(_DEFAULT_DELIVERY_TTL_S)))


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


__all__ = ["upload_reel"]
