"""AF-4pz.6 — project asset download route (302 to a fetchable URL).

Mirrors the slide route's T10 discipline: the browser only ever gets
server-provided fetchable URLs — links 302 to their stored URL; bucket-backed
assets 302 to a presigned GET; video-reuse assets presign the SOURCE ASSET's
key. The project resolves first, so foreign/absent anything is a 404 before
any presign.
"""

from __future__ import annotations

import uuid

import server
from conftest import (
    FakeIdentity,
    FakeProjectAssetRepo,
    FakeProjectRepo,
    FakeSourceAssetRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from deps import SchemaUnavailable
from projects import ProjectRef
from source_assets import SourceAssetRef

ORG_ID = make_ctx().org_id
USER_ID = make_ctx().user_id
PROJECT_ID = uuid.UUID("aaaa0000-0000-4000-8000-000000000001")
SOURCE_ASSET_ID = uuid.UUID("bbbb0000-0000-4000-8000-000000000002")
PRESIGNED = "https://bucket.example/signed/asset?sig=abc"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _project():
    return ProjectRef(project_id=PROJECT_ID, org_id=ORG_ID, created_by=USER_ID, name="P")


def _source_asset():
    return SourceAssetRef(
        asset_id=SOURCE_ASSET_ID, org_id=ORG_ID, created_by=USER_ID,
        bucket_key=f"{ORG_ID}/reused-clip.mp4", original_filename="clip.mp4",
        content_type="video/mp4", size_bytes=9, checksum="sha256:x", status="stored",
    )


def _deps(*, source_assets=None):
    return make_deps(
        identity=FakeIdentity(make_ctx("member")),
        uploads=FakeUploadStore(presigned=PRESIGNED),
        source_assets=FakeSourceAssetRepo(assets=source_assets or []),
        projects=FakeProjectRepo(projects=[_project()]),
        project_assets=FakeProjectAssetRepo(),
    )


def _add(deps, payload=None, **json_body):
    body = payload or json_body
    resp = _client(deps).post(
        f"/api/v1/projects/{PROJECT_ID}/assets", json=body
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["asset_id"]


def _download_url(asset_id):
    return f"/api/v1/projects/{PROJECT_ID}/assets/{asset_id}/download"


def test_link_asset_redirects_to_stored_url():
    deps = _deps()
    asset_id = _add(deps, asset_type="link", url="https://example.com/doc")

    resp = _client(deps).get(_download_url(asset_id))

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://example.com/doc"


def test_bucket_asset_redirects_to_presigned_url():
    deps = _deps()
    # An image added via multipart records a bucket_key; emulate through the
    # fake repo directly (the add route is covered by test_projects_api).
    ref = deps.project_assets.add(
        make_ctx("member"), asset_id=uuid.uuid4(), project_id=PROJECT_ID,
        asset_type="image", source_asset_id=None,
        bucket_key=f"{ORG_ID}/img.png", url=None, title=None, now=None,
    )

    resp = _client(deps).get(_download_url(ref.asset_id))

    assert resp.status_code == 302
    assert resp.headers["Location"] == PRESIGNED
    assert deps.uploads.presign_calls == [(ORG_ID, f"{ORG_ID}/img.png")]


def test_video_reuse_asset_presigns_source_asset_key():
    deps = _deps(source_assets=[_source_asset()])
    asset_id = _add(deps, asset_type="video", source_asset_id=str(SOURCE_ASSET_ID))

    resp = _client(deps).get(_download_url(asset_id))

    assert resp.status_code == 302
    assert resp.headers["Location"] == PRESIGNED
    assert deps.uploads.presign_calls == [(ORG_ID, f"{ORG_ID}/reused-clip.mp4")]


def test_foreign_project_or_absent_asset_is_404_no_presign():
    deps = _deps()
    missing = uuid.uuid4()

    resp = _client(deps).get(_download_url(missing))
    assert resp.status_code == 404

    foreign_project = _client(deps).get(
        f"/api/v1/projects/{uuid.uuid4()}/assets/{missing}/download"
    )
    assert foreign_project.status_code == 404
    assert deps.uploads.presign_calls == []


def test_pg_asset_get_fails_closed_without_database_url(monkeypatch):
    import pytest
    from pg import PgProjectAssetRepo

    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    with pytest.raises(SchemaUnavailable):
        PgProjectAssetRepo().get(make_ctx(), PROJECT_ID, uuid.uuid4())
