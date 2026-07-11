"""Authenticated upload store for reel-af-ui (plan B8).

The former proxy forwarded multipart uploads straight to the control plane. This
replaces that with an authenticated local ingress: the caller is already verified
+ authorized (create) by the route layer; the store validates the file and writes
it under an org-scoped path, returning a handle compatible with the browser's
``uploadFile()`` (``{"path": ...}``) that then feeds a composite file submit.

Fail-closed: with no ``REEL_UPLOAD_DIR`` configured, ``ensure_ready`` raises
``SchemaUnavailable`` (503) and no file is written.
"""

from __future__ import annotations

import os
import re
import uuid

from deps import BadRequest, NotFound, PayloadTooLarge, SchemaUnavailable

_DEFAULT_MAX_MIB = 512
_DEFAULT_PRESIGN_TTL_S = 3600  # signed GET URL lifetime handed to the reel-af node (T7)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _max_bytes() -> int:
    return int(os.getenv("REEL_UPLOAD_MAX_MIB", str(_DEFAULT_MAX_MIB))) * 1024 * 1024


def _presign_ttl_s() -> int:
    return int(os.getenv("REEL_PRESIGN_TTL_S", str(_DEFAULT_PRESIGN_TTL_S)))


def _measure(stream) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", os.path.basename(name or "")).strip("._") or "upload.bin"
    return cleaned[:120]


def _object_key(ctx, filename: str) -> str:
    return f"{ctx.org_id}/{uuid.uuid4().hex}-{_safe_filename(filename)}"


def _belongs_to_org(ctx, handle: str) -> bool:
    """True iff the upload key is under the caller's org prefix (Phase 0 ownership)."""
    return isinstance(handle, str) and handle.strip().startswith(f"{ctx.org_id}/")


class LocalUploadStore:
    """Writes uploads under ``REEL_UPLOAD_DIR/<org_id>/<uuid>-<name>``."""

    def _dir(self) -> str:
        root = os.getenv("REEL_UPLOAD_DIR", "")
        if not root:
            raise SchemaUnavailable("upload storage not configured (REEL_UPLOAD_DIR)")
        return root

    def ensure_ready(self) -> None:
        self._dir()

    def store(self, ctx, file_storage) -> dict:
        root = self._dir()
        if file_storage is None or not getattr(file_storage, "filename", ""):
            raise BadRequest("no file in multipart field 'file'", code="no_file")

        if _measure(file_storage.stream) > _max_bytes():
            raise PayloadTooLarge(f"file exceeds {_max_bytes()} bytes")

        key = _object_key(ctx, file_storage.filename)
        dest = os.path.join(root, key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        file_storage.save(dest)
        return {"path": key}

    def presign(self, ctx, handle: str) -> str:
        # A local-volume file is not reachable by the separate reel-af node; file
        # mode requires shared object storage (BucketUploadStore). Fail closed so
        # the caller returns 503 rather than dispatching an unfetchable path (T7).
        self._dir()
        raise SchemaUnavailable(
            "local upload storage cannot presign a node-reachable URL; "
            "configure a shared bucket (REEL_BUCKET_*) for file-mode composites"
        )


class BucketUploadStore:
    """S3-compatible object-store ingress (T7). Writes uploads to a shared bucket
    the reel-af node can fetch, and presigns a time-limited GET URL for dispatch.

    The stored handle is the opaque object key (``<org_id>/<uuid>-<name>``) — same
    ``{"path": ...}`` shape the browser already round-trips. The client never sees a
    URL; the server presigns the key at submit time (:meth:`presign`).

    Fail-closed: with no ``REEL_BUCKET_NAME`` configured, ``ensure_ready``/``presign``
    raise ``SchemaUnavailable`` (503) and nothing is written or dispatched.
    """

    def __init__(self, client_factory=None):
        # Injectable for tests; production builds a boto3 S3 client lazily so import
        # stays side-effect-free (B1) and boto3 is only needed at request time.
        self._client_factory = client_factory

    def _bucket(self) -> str:
        name = os.getenv("REEL_BUCKET_NAME", "")
        if not name:
            raise SchemaUnavailable("upload storage not configured (REEL_BUCKET_NAME)")
        return name

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import boto3  # lazy: only pulled in when a request actually touches the store

        return boto3.client(
            "s3",
            endpoint_url=os.getenv("REEL_BUCKET_ENDPOINT") or None,
            aws_access_key_id=os.getenv("REEL_BUCKET_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.getenv("REEL_BUCKET_SECRET_ACCESS_KEY") or None,
            region_name=os.getenv("REEL_BUCKET_REGION", "auto"),
        )

    def ensure_ready(self) -> None:
        self._bucket()

    def store(self, ctx, file_storage) -> dict:
        bucket = self._bucket()
        if file_storage is None or not getattr(file_storage, "filename", ""):
            raise BadRequest("no file in multipart field 'file'", code="no_file")

        if _measure(file_storage.stream) > _max_bytes():
            raise PayloadTooLarge(f"file exceeds {_max_bytes()} bytes")

        key = _object_key(ctx, file_storage.filename)
        self._client().upload_fileobj(file_storage.stream, bucket, key)
        return {"path": key}

    def presign(self, ctx, handle: str) -> str:
        bucket = self._bucket()
        if not isinstance(handle, str) or not handle.strip():
            raise BadRequest("missing upload handle", code="missing_source")
        # Ownership boundary (Phase 0): only presign keys under the caller's org.
        # Defense-in-depth behind the server-level guard; conceal foreign keys as 404.
        if not _belongs_to_org(ctx, handle):
            raise NotFound("upload handle not found", code="upload_not_found")
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": handle.strip()},
            ExpiresIn=_presign_ttl_s(),
        )
