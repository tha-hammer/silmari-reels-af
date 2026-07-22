"""Source-asset domain model (AF-02f).

An upload is a durable, reusable ASSET, not an ephemeral handle: the upload
route writes an org-scoped, owner-stamped ``deepresearch.source_asset`` row
(server-derived identity, never client-supplied) and returns a stable
``asset_id`` alongside the existing ``{"path": ...}`` handle. This module is
PURE — metadata extraction and view shaping only; SQL lives in ``pg.py`` and
route wiring in ``server.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_HASH_CHUNK_BYTES = 1 << 20


@dataclass
class SourceAssetRef:
    """One persisted upload record (mirror of a ``source_asset`` row)."""

    asset_id: Any
    org_id: str
    created_by: str
    bucket_key: str
    original_filename: str
    content_type: str | None
    size_bytes: int
    checksum: str
    status: str = "stored"
    created_at: datetime | None = None


def describe_upload(file_storage) -> dict | None:
    """Pure metadata of a multipart upload BEFORE the store consumes the stream.

    Streams the file once for a ``sha256:<hex>`` checksum + byte-accurate size,
    then seeks back to 0 so the store still writes the full bytes. Returns
    ``None`` when no file is present — the store then raises the canonical 400.
    """
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return None
    stream = file_storage.stream
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(_HASH_CHUNK_BYTES):
        digest.update(chunk)
        size += len(chunk)
    stream.seek(0)
    return {
        "original_filename": file_storage.filename,
        "content_type": getattr(file_storage, "mimetype", None) or None,
        "size_bytes": size,
        "checksum": f"sha256:{digest.hexdigest()}",
    }


def asset_view(ref: SourceAssetRef) -> dict:
    """Browser-facing shape: the record plus the reusable submit handle
    (``path`` — the same org-scoped key the upload response returns)."""
    return {
        "asset_id": str(ref.asset_id),
        "path": ref.bucket_key,
        "original_filename": ref.original_filename,
        "content_type": ref.content_type,
        "size_bytes": ref.size_bytes,
        "checksum": ref.checksum,
        "status": ref.status,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
    }
