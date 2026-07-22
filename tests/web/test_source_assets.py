"""AF-02f — uploads persist as first-class ``source_asset`` records.

An upload is a durable, reusable ASSET: the upload route writes an org-scoped,
owner-stamped record (server-derived identity, never client-supplied) and
returns a stable ``asset_id`` alongside the existing ``{"path": ...}`` handle
(T7 contract unchanged). A list route exposes the caller's assets.
"""

from __future__ import annotations

import hashlib
import io

import server
from conftest import (
    FIXED_JOB_ID,
    FakeIdentity,
    FakeSourceAssetRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from deps import SchemaUnavailable, Unauthorized
from source_assets import SourceAssetRef

UPLOAD_URL = "/api/v1/uploads"
LIST_URL = "/api/v1/source-assets"
ORG_ID = make_ctx().org_id
USER_ID = make_ctx().user_id
VIDEO_BYTES = b"video-bytes-for-af02f"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _multipart(data=VIDEO_BYTES, name="clip.mp4"):
    return {"file": (io.BytesIO(data), name)}


# ── Behavior 1: upload writes the record and returns asset_id ────────


def test_upload_persists_source_asset_and_returns_asset_id():
    repo = FakeSourceAssetRepo()
    uploads = FakeUploadStore()
    deps = make_deps(
        identity=FakeIdentity(make_ctx("member")), uploads=uploads, source_assets=repo
    )

    resp = _client(deps).post(
        UPLOAD_URL, data=_multipart(), content_type="multipart/form-data"
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert "path" in body                       # T7 contract unchanged
    assert body["asset_id"] == str(FIXED_JOB_ID)

    assert len(repo.created) == 1
    rec = repo.created[0]
    # Server-derived identity stamps — never client-supplied.
    assert rec["ctx"].org_id == ORG_ID
    assert rec["ctx"].user_id == USER_ID
    assert rec["bucket_key"] == body["path"]
    assert rec["original_filename"] == "clip.mp4"
    assert rec["content_type"] == "video/mp4"
    assert rec["size_bytes"] == len(VIDEO_BYTES)
    assert rec["checksum"] == "sha256:" + hashlib.sha256(VIDEO_BYTES).hexdigest()


def test_upload_stream_still_stored_intact_after_checksum():
    """describe_upload must seek back so the store writes the full bytes."""
    uploads = FakeUploadStore()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), uploads=uploads)

    resp = _client(deps).post(
        UPLOAD_URL, data=_multipart(), content_type="multipart/form-data"
    )

    assert resp.status_code == 201
    assert uploads.stored_bytes == [VIDEO_BYTES]


# ── Behaviors 2-4: failure ordering (no phantom records) ─────────────


def test_no_file_is_400_and_writes_no_record():
    repo = FakeSourceAssetRepo()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), source_assets=repo)
    resp = _client(deps).post(UPLOAD_URL, data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert repo.created == []


def test_store_failure_is_503_and_writes_no_record():
    repo = FakeSourceAssetRepo()
    uploads = FakeUploadStore(error=SchemaUnavailable("bucket not configured"))
    deps = make_deps(
        identity=FakeIdentity(make_ctx("member")), uploads=uploads, source_assets=repo
    )
    resp = _client(deps).post(
        UPLOAD_URL, data=_multipart(), content_type="multipart/form-data"
    )
    assert resp.status_code == 503
    assert repo.created == []


def test_repo_failure_after_store_is_503():
    """Fail-closed: the asset record is REQUIRED — a stored object with no DB
    row returns 503 rather than silently succeeding without persistence."""
    repo = FakeSourceAssetRepo(create_error=SchemaUnavailable("schema not applied"))
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), source_assets=repo)
    resp = _client(deps).post(
        UPLOAD_URL, data=_multipart(), content_type="multipart/form-data"
    )
    assert resp.status_code == 503


# ── Behavior 5: list route ───────────────────────────────────────────


def _ref(asset_id="a5a5a5a5-0000-4000-8000-000000000001", filename="clip.mp4"):
    return SourceAssetRef(
        asset_id=asset_id, org_id=ORG_ID, created_by=USER_ID,
        bucket_key=f"{ORG_ID}/{asset_id}-{filename}", original_filename=filename,
        content_type="video/mp4", size_bytes=21, checksum="sha256:abc",
        status="stored",
    )


def test_list_returns_caller_assets():
    repo = FakeSourceAssetRepo(assets=[_ref(), _ref(asset_id="a5a5a5a5-0000-4000-8000-000000000002", filename="b.mp4")])
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), source_assets=repo)

    resp = _client(deps).get(LIST_URL)

    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["assets"]) == 2
    first = body["assets"][0]
    assert first["asset_id"] == "a5a5a5a5-0000-4000-8000-000000000001"
    assert first["path"] == _ref().bucket_key   # reusable submit handle
    assert first["original_filename"] == "clip.mp4"
    assert first["size_bytes"] == 21
    assert first["checksum"] == "sha256:abc"
    # Org-scoping is enforced in the repo with the resolved ctx.
    assert [c.org_id for c in repo.list_calls] == [ORG_ID]


def test_list_without_session_is_401():
    deps = make_deps(identity=FakeIdentity(error=Unauthorized("no session")))
    assert _client(deps).get(LIST_URL).status_code == 401


# ── Behaviors 6-7: schema surface + fail-closed repo ─────────────────


def test_feature_schema_includes_source_asset_columns():
    from pg import FEATURE_SCHEMA

    assert FEATURE_SCHEMA["source_asset"] >= {
        "id", "org_id", "created_by", "bucket_key", "original_filename",
        "content_type", "size_bytes", "checksum", "status", "created_at",
        "deleted_at",
    }


def test_pg_repo_fails_closed_without_database_url(monkeypatch):
    from pg import PgSourceAssetRepo

    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    import pytest

    with pytest.raises(SchemaUnavailable):
        PgSourceAssetRepo().create(
            make_ctx(), asset_id=FIXED_JOB_ID, bucket_key="k",
            original_filename="f", content_type=None, size_bytes=1,
            checksum="sha256:x", now=None,
        )
    with pytest.raises(SchemaUnavailable):
        PgSourceAssetRepo().list_for_org(make_ctx())
