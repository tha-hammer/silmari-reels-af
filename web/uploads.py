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

from deps import BadRequest, PayloadTooLarge, SchemaUnavailable

_DEFAULT_MAX_MIB = 512
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _max_bytes() -> int:
    return int(os.getenv("REEL_UPLOAD_MAX_MIB", str(_DEFAULT_MAX_MIB))) * 1024 * 1024


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", os.path.basename(name or "")).strip("._") or "upload.bin"
    return cleaned[:120]


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

        stream = file_storage.stream
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        if size > _max_bytes():
            raise PayloadTooLarge(f"file exceeds {_max_bytes()} bytes")

        org_dir = os.path.join(root, str(ctx.org_id))
        os.makedirs(org_dir, exist_ok=True)
        name = f"{uuid.uuid4().hex}-{_safe_filename(file_storage.filename)}"
        dest = os.path.join(org_dir, name)
        file_storage.save(dest)
        return {"path": os.path.join(str(ctx.org_id), name)}
