"""B8 - authenticated upload contract (real LocalUploadStore, no CP call)."""

from __future__ import annotations

import io

import server
from conftest import FakeControlPlane, FakeIdentity, make_ctx, make_deps
from deps import Unauthorized
from uploads import LocalUploadStore

UPLOAD_URL = "/api/v1/uploads"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _multipart(data=b"video-bytes", name="clip.mp4"):
    return {"file": (io.BytesIO(data), name)}


def test_upload_success_returns_path_handle(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_UPLOAD_DIR", str(tmp_path))
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), control_plane=cp)
    deps.uploads = LocalUploadStore()

    resp = _client(deps).post(UPLOAD_URL, data=_multipart(), content_type="multipart/form-data")

    assert resp.status_code == 201
    assert "path" in resp.get_json()
    assert cp.dispatch_calls == [] and cp.get_calls == []  # upload never calls CP


def test_upload_without_session_is_401(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_UPLOAD_DIR", str(tmp_path))
    deps = make_deps(identity=FakeIdentity(error=Unauthorized("no session")))
    deps.uploads = LocalUploadStore()
    resp = _client(deps).post(UPLOAD_URL, data=_multipart(), content_type="multipart/form-data")
    assert resp.status_code == 401


def test_upload_viewer_is_403(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_UPLOAD_DIR", str(tmp_path))
    deps = make_deps(identity=FakeIdentity(make_ctx("viewer")))
    deps.uploads = LocalUploadStore()
    resp = _client(deps).post(UPLOAD_URL, data=_multipart(), content_type="multipart/form-data")
    assert resp.status_code == 403


def test_upload_no_file_is_400(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_UPLOAD_DIR", str(tmp_path))
    deps = make_deps(identity=FakeIdentity(make_ctx("member")))
    deps.uploads = LocalUploadStore()
    resp = _client(deps).post(UPLOAD_URL, data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_too_large_is_413(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_UPLOAD_MAX_MIB", "0")  # everything exceeds 0
    deps = make_deps(identity=FakeIdentity(make_ctx("member")))
    deps.uploads = LocalUploadStore()
    resp = _client(deps).post(UPLOAD_URL, data=_multipart(b"x" * 10), content_type="multipart/form-data")
    assert resp.status_code == 413


def test_upload_storage_unconfigured_is_503(monkeypatch):
    monkeypatch.delenv("REEL_UPLOAD_DIR", raising=False)
    deps = make_deps(identity=FakeIdentity(make_ctx("member")))
    deps.uploads = LocalUploadStore()
    resp = _client(deps).post(UPLOAD_URL, data=_multipart(), content_type="multipart/form-data")
    assert resp.status_code == 503
