"""T7 - BucketUploadStore: S3-compatible object ingress + presigned GET URLs.

Uses an injected fake S3 client (client_factory) so no boto3 / network is needed.
Mirrors the LocalUploadStore contract in test_upload.py: validate → store an
org-scoped opaque key handle; presign it to a node-fetchable URL; fail closed
(503) when the bucket is unconfigured.
"""

from __future__ import annotations

import io

import pytest
from conftest import make_ctx
from deps import BadRequest, NotFound, PayloadTooLarge, SchemaUnavailable
from uploads import BucketUploadStore

BUCKET = "reel-uploads-test"


class FakeS3:
    def __init__(self):
        self.uploaded: list = []
        self.presigned: list = []

    def upload_fileobj(self, fileobj, bucket, key):
        self.uploaded.append((bucket, key, fileobj.read()))

    def generate_presigned_url(self, operation, Params, ExpiresIn):  # noqa: N803 (boto3 kwarg name)
        assert operation == "get_object"
        self.presigned.append((Params["Bucket"], Params["Key"], ExpiresIn))
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


class FakeFile:
    def __init__(self, data=b"video-bytes", filename="clip.mp4"):
        self.filename = filename
        self.stream = io.BytesIO(data)


def _store(monkeypatch, client=None, *, configured=True):
    if configured:
        monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    else:
        monkeypatch.delenv("REEL_BUCKET_NAME", raising=False)
    return BucketUploadStore(client_factory=(lambda: client) if client else None)


def test_store_uploads_object_and_returns_org_scoped_key(monkeypatch):
    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    ctx = make_ctx("member")

    handle = store.store(ctx, FakeFile(b"abc", "My Clip.mp4"))

    assert set(handle) == {"path"}
    key = handle["path"]
    assert key.startswith(f"{ctx.org_id}/")            # org-scoped
    assert key.endswith("-My_Clip.mp4")                # filename sanitized
    assert s3.uploaded == [(BUCKET, key, b"abc")]      # written to the bucket under that key


def test_presign_returns_node_fetchable_url_for_ctx_owned_handle(monkeypatch):
    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    ctx = make_ctx("member")
    handle = f"{ctx.org_id}/uuid-clip.mp4"

    url = store.presign(ctx, handle)

    assert url == f"https://s3.example/{BUCKET}/{handle}?X-Amz-Expires=3600"


def test_presign_rejects_foreign_handle_without_calling_s3(monkeypatch):
    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    ctx = make_ctx("member")

    with pytest.raises(NotFound):
        store.presign(ctx, "11111111-2222-3333-4444-555555555555/uuid-clip.mp4")  # foreign org
    assert s3.presigned == []                            # never presigned a foreign key


def test_presign_ttl_is_configurable(monkeypatch):
    monkeypatch.setenv("REEL_PRESIGN_TTL_S", "120")
    store = _store(monkeypatch, FakeS3())
    ctx = make_ctx("member")
    assert store.presign(ctx, f"{ctx.org_id}/obj.mp4").endswith("X-Amz-Expires=120")


def test_store_unconfigured_is_503(monkeypatch):
    store = _store(monkeypatch, FakeS3(), configured=False)
    with pytest.raises(SchemaUnavailable):
        store.store(make_ctx("member"), FakeFile())


def test_presign_unconfigured_is_503(monkeypatch):
    store = _store(monkeypatch, FakeS3(), configured=False)
    ctx = make_ctx("member")
    with pytest.raises(SchemaUnavailable):
        store.presign(ctx, f"{ctx.org_id}/obj.mp4")


def test_store_no_file_is_400(monkeypatch):
    store = _store(monkeypatch, FakeS3())
    with pytest.raises(BadRequest):
        store.store(make_ctx("member"), None)


def test_store_too_large_is_413(monkeypatch):
    monkeypatch.setenv("REEL_UPLOAD_MAX_MIB", "0")   # everything exceeds 0
    store = _store(monkeypatch, FakeS3())
    with pytest.raises(PayloadTooLarge):
        store.store(make_ctx("member"), FakeFile(b"x" * 10))


def test_presign_empty_handle_is_400(monkeypatch):
    store = _store(monkeypatch, FakeS3())
    with pytest.raises(BadRequest):
        store.presign(make_ctx("member"), "   ")
