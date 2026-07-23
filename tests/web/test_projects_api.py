"""AF-4pz.4 + AF-4pz.5 — Projects CRUD + project assets API.

Org-scoped, owner-stamped Projects behind the existing tenancy boundary, and
attach/list/remove of project assets (video reuse-or-upload, image/document
upload via the existing upload store, validated link URLs). Foreign/absent
projects and assets are concealed as 404; identity is always server-derived.
"""

from __future__ import annotations

import io
import uuid

import server
from conftest import (
    FIXED_JOB_ID,
    FakeIdentity,
    FakeProjectAssetRepo,
    FakeProjectRepo,
    FakeSourceAssetRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from deps import SchemaUnavailable, Unauthorized
from projects import ProjectRef
from source_assets import SourceAssetRef

PROJECTS_URL = "/api/v1/projects"
ORG_ID = make_ctx().org_id
USER_ID = make_ctx().user_id
PROJECT_ID = uuid.UUID("aaaa0000-0000-4000-8000-000000000001")
SOURCE_ASSET_ID = uuid.UUID("bbbb0000-0000-4000-8000-000000000002")


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _project(project_id=PROJECT_ID, org_id=ORG_ID, name="My Project"):
    return ProjectRef(
        project_id=project_id, org_id=org_id, created_by=USER_ID,
        name=name, description=None,
    )


def _source_asset():
    return SourceAssetRef(
        asset_id=SOURCE_ASSET_ID, org_id=ORG_ID, created_by=USER_ID,
        bucket_key=f"{ORG_ID}/clip.mp4", original_filename="clip.mp4",
        content_type="video/mp4", size_bytes=9, checksum="sha256:x",
        status="stored",
    )


def _deps(*, projects=None, assets=None, source_assets=None, uploads=None, identity=None):
    return make_deps(
        identity=identity or FakeIdentity(make_ctx("member")),
        uploads=uploads or FakeUploadStore(),
        source_assets=FakeSourceAssetRepo(assets=source_assets or []),
        projects=projects or FakeProjectRepo(),
        project_assets=assets or FakeProjectAssetRepo(),
    )


# ── AF-4pz.4: Projects CRUD ──────────────────────────────────────────


def test_create_project_stamps_identity_and_returns_id():
    repo = FakeProjectRepo()
    deps = _deps(projects=repo)

    resp = _client(deps).post(PROJECTS_URL, json={"name": "  Reels Q3  ", "description": "d"})

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["project_id"] == str(FIXED_JOB_ID)
    assert body["name"] == "Reels Q3"
    rec = repo.created[0]
    assert rec["ctx"].org_id == ORG_ID and rec["ctx"].user_id == USER_ID
    assert rec["name"] == "Reels Q3"


def test_create_project_requires_name():
    resp = _client(_deps()).post(PROJECTS_URL, json={"name": "   "})
    assert resp.status_code == 400


def test_create_project_viewer_is_403():
    deps = _deps(identity=FakeIdentity(make_ctx("viewer")))
    assert _client(deps).post(PROJECTS_URL, json={"name": "x"}).status_code == 403


def test_list_projects_is_org_scoped_via_repo():
    repo = FakeProjectRepo(projects=[_project()])
    deps = _deps(projects=repo)

    resp = _client(deps).get(PROJECTS_URL)

    assert resp.status_code == 200
    assert [p["project_id"] for p in resp.get_json()["projects"]] == [str(PROJECT_ID)]
    assert [c.org_id for c in repo.list_calls] == [ORG_ID]


def test_get_foreign_or_absent_project_is_404():
    deps = _deps(projects=FakeProjectRepo())   # owns nothing
    assert _client(deps).get(f"{PROJECTS_URL}/{PROJECT_ID}").status_code == 404


def test_patch_renames_project():
    repo = FakeProjectRepo(projects=[_project()])
    deps = _deps(projects=repo)

    resp = _client(deps).patch(f"{PROJECTS_URL}/{PROJECT_ID}", json={"name": "Renamed"})

    assert resp.status_code == 200
    assert resp.get_json()["name"] == "Renamed"
    assert repo.updated[0][1] == PROJECT_ID


def test_delete_is_soft_and_returns_204():
    repo = FakeProjectRepo(projects=[_project()])
    deps = _deps(projects=repo)

    resp = _client(deps).delete(f"{PROJECTS_URL}/{PROJECT_ID}")

    assert resp.status_code == 204
    assert repo.soft_deleted == [PROJECT_ID]
    assert _client(deps).get(f"{PROJECTS_URL}/{PROJECT_ID}").status_code == 404


def test_projects_without_session_is_401():
    deps = _deps(identity=FakeIdentity(error=Unauthorized("no session")))
    assert _client(deps).get(PROJECTS_URL).status_code == 401


def test_pg_project_repo_fails_closed_without_database_url(monkeypatch):
    import pytest
    from pg import PgProjectRepo

    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    with pytest.raises(SchemaUnavailable):
        PgProjectRepo().list_for_org(make_ctx())


# ── AF-4pz.5: project assets ─────────────────────────────────────────


def _assets_url(project_id=PROJECT_ID):
    return f"{PROJECTS_URL}/{project_id}/assets"


def test_add_link_asset_validates_url_and_records_row():
    projects = FakeProjectRepo(projects=[_project()])
    assets = FakeProjectAssetRepo()
    deps = _deps(projects=projects, assets=assets)

    resp = _client(deps).post(
        _assets_url(), json={"asset_type": "link", "url": "https://example.com/doc", "title": "Doc"}
    )

    assert resp.status_code == 201
    rec = assets.added[0]
    assert rec["asset_type"] == "link"
    assert rec["url"] == "https://example.com/doc"
    assert rec["source_asset_id"] is None and rec["bucket_key"] is None
    assert rec["project_id"] == PROJECT_ID
    assert rec["ctx"].org_id == ORG_ID


def test_add_link_asset_rejects_invalid_url():
    deps = _deps(projects=FakeProjectRepo(projects=[_project()]))
    resp = _client(deps).post(_assets_url(), json={"asset_type": "link", "url": "notaurl"})
    assert resp.status_code == 400


def test_add_video_asset_by_source_asset_reuse():
    projects = FakeProjectRepo(projects=[_project()])
    assets = FakeProjectAssetRepo()
    deps = _deps(projects=projects, assets=assets, source_assets=[_source_asset()])

    resp = _client(deps).post(
        _assets_url(),
        json={"asset_type": "video", "source_asset_id": str(SOURCE_ASSET_ID)},
    )

    assert resp.status_code == 201
    rec = assets.added[0]
    assert rec["asset_type"] == "video"
    assert str(rec["source_asset_id"]) == str(SOURCE_ASSET_ID)
    assert rec["bucket_key"] is None and rec["url"] is None


def test_add_video_asset_foreign_source_asset_is_404():
    deps = _deps(projects=FakeProjectRepo(projects=[_project()]), source_assets=[])
    resp = _client(deps).post(
        _assets_url(),
        json={"asset_type": "video", "source_asset_id": str(SOURCE_ASSET_ID)},
    )
    assert resp.status_code == 404


def test_add_image_asset_uploads_via_store_and_records_bucket_key():
    projects = FakeProjectRepo(projects=[_project()])
    assets = FakeProjectAssetRepo()
    uploads = FakeUploadStore(handle={"path": f"{ORG_ID}/img.png"})
    deps = _deps(projects=projects, assets=assets, uploads=uploads)

    resp = _client(deps).post(
        _assets_url(),
        data={"asset_type": "image", "file": (io.BytesIO(b"png-bytes"), "img.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    rec = assets.added[0]
    assert rec["asset_type"] == "image"
    assert rec["bucket_key"] == f"{ORG_ID}/img.png"
    assert rec["source_asset_id"] is None and rec["url"] is None
    assert uploads.stored_bytes == [b"png-bytes"]


def test_add_asset_to_foreign_project_is_404_before_store():
    uploads = FakeUploadStore()
    deps = _deps(projects=FakeProjectRepo(), uploads=uploads)  # owns no project

    resp = _client(deps).post(
        _assets_url(),
        data={"asset_type": "image", "file": (io.BytesIO(b"x"), "i.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 404
    assert uploads.stored_bytes == []


def test_add_asset_unknown_type_is_400():
    deps = _deps(projects=FakeProjectRepo(projects=[_project()]))
    resp = _client(deps).post(_assets_url(), json={"asset_type": "audio", "url": "https://x.com"})
    assert resp.status_code == 400


def test_list_assets_scoped_to_project():
    projects = FakeProjectRepo(projects=[_project()])
    assets = FakeProjectAssetRepo()
    deps = _deps(projects=projects, assets=assets)
    _client(deps).post(
        _assets_url(), json={"asset_type": "link", "url": "https://example.com"}
    )

    resp = _client(deps).get(_assets_url())

    assert resp.status_code == 200
    listed = resp.get_json()["assets"]
    assert len(listed) == 1
    assert listed[0]["asset_type"] == "link"
    assert assets.list_calls[-1][1] == PROJECT_ID


def test_remove_asset_is_soft_delete_204():
    projects = FakeProjectRepo(projects=[_project()])
    assets = FakeProjectAssetRepo()
    deps = _deps(projects=projects, assets=assets)
    created = _client(deps).post(
        _assets_url(), json={"asset_type": "link", "url": "https://example.com"}
    ).get_json()

    resp = _client(deps).delete(f"{_assets_url()}/{created['asset_id']}")

    assert resp.status_code == 204
    assert [str(a) for a in assets.soft_deleted] == [created["asset_id"]]
