"""Object-storage media adapter (P0). Mirrors uploads.BucketUploadStore: REEL_BUCKET_*
env, lazy boto3 client, org-scoped keys, presigned GET URLs, fail-closed 503.

The stored ref is the S3 Key itself — an org-prefixed, parseable ``<org_id>/<key>``
(not opaque). The org prefix is a load-bearing invariant callers/observability may
read. ``put`` is last-write-wins on a stable ref (same ``(org_id, key)`` → same Key).
``delete`` is intentionally absent — Plan 6 forward-extension (object cleanup).
"""

from __future__ import annotations

import os

from deps import BadRequest, SchemaUnavailable

_DEFAULT_PRESIGN_TTL_S = 3600
_MEDIA_KEY_SEP = "/"


def _presign_ttl_s() -> int:
    return int(os.getenv("REEL_PRESIGN_TTL_S", str(_DEFAULT_PRESIGN_TTL_S)))


def _s3_client_from_env(client_factory=None):
    """Build an S3-compatible client from ``REEL_BUCKET_*`` env, or delegate to an
    injected ``client_factory`` (tests). The single boto3-client construction point
    shared by ``ObjectStorage`` and ``uploads.BucketUploadStore`` — no copy-paste.
    Lazy boto3 import keeps module import side-effect-free (B1)."""
    if client_factory is not None:
        return client_factory()
    import boto3  # lazy: only pulled in when a request actually touches a store

    return boto3.client(
        "s3",
        endpoint_url=os.getenv("REEL_BUCKET_ENDPOINT") or None,
        aws_access_key_id=os.getenv("REEL_BUCKET_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.getenv("REEL_BUCKET_SECRET_ACCESS_KEY") or None,
        region_name=os.getenv("REEL_BUCKET_REGION", "auto"),
    )


class ObjectStorage:
    """S3-compatible media store. ``client_factory`` is injectable for tests (no boto3)."""

    def __init__(self, client_factory=None):
        # Injectable for tests; production builds a boto3 client lazily so import stays
        # side-effect-free (B1) and boto3 is only needed when a request touches the store.
        self._client_factory = client_factory

    def _bucket(self) -> str:
        name = os.getenv("REEL_BUCKET_NAME", "")
        if not name:
            raise SchemaUnavailable("media storage not configured (REEL_BUCKET_NAME)")
        return name

    def _client(self):
        return _s3_client_from_env(self._client_factory)

    def _ref(self, org_id, key: str) -> str:
        return f"{org_id}{_MEDIA_KEY_SEP}{key.lstrip(_MEDIA_KEY_SEP)}"

    def put(self, org_id, key: str, data) -> str:
        bucket = self._bucket()
        ref = self._ref(org_id, key)
        body = data if isinstance(data, (bytes, bytearray)) else data.read()
        self._client().put_object(Bucket=bucket, Key=ref, Body=body)
        return ref

    def presigned_url(self, ref: str, ttl: int | None = None) -> str:
        bucket = self._bucket()
        if not isinstance(ref, str) or not ref.strip():
            raise BadRequest("missing media ref", code="missing_ref")
        expires = ttl if ttl is not None else _presign_ttl_s()
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": ref.strip()},
            ExpiresIn=expires,
        )

    def exists(self, ref: str) -> bool:
        bucket = self._bucket()
        if not isinstance(ref, str) or not ref.strip():
            return False
        try:
            self._client().head_object(Bucket=bucket, Key=ref.strip())
            return True
        except Exception:  # boundary: any head_object miss/error → absent
            return False
