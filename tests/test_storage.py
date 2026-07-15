"""T10: storage.upload_reel — deliver produced reels out to the shared bucket.

Injected fake S3 client (no boto3 / network). Fail-soft when unconfigured or missing.
"""

from __future__ import annotations

from reel_af import storage

BUCKET = "reel-uploads-test"


class FakeS3:
    def __init__(self):
        self.uploaded: list = []
        self.presigned: list = []

    def upload_file(self, filename, bucket, key):
        self.uploaded.append((filename, bucket, key))

    def generate_presigned_url(self, operation, Params, ExpiresIn):  # noqa: N803 (boto3 kwarg)
        assert operation == "get_object"
        self.presigned.append((Params["Bucket"], Params["Key"], ExpiresIn))
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


def test_upload_reel_returns_presigned_url_under_run_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    reel = tmp_path / "reel01.mp4"
    reel.write_bytes(b"video-bytes")
    s3 = FakeS3()

    url = storage.upload_reel(reel, run_id="abc123", client_factory=lambda: s3)

    assert url == f"https://s3.example/{BUCKET}/outputs/abc123/reel01.mp4?X-Amz-Expires=86400"
    assert s3.uploaded == [(str(reel), BUCKET, "outputs/abc123/reel01.mp4")]


def test_upload_reel_ttl_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("REEL_DELIVERY_TTL_S", "120")
    reel = tmp_path / "r.mp4"
    reel.write_bytes(b"v")
    assert storage.upload_reel(reel, run_id="x", client_factory=FakeS3).endswith("X-Amz-Expires=120")


def test_upload_reel_none_when_bucket_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("REEL_BUCKET_NAME", raising=False)
    reel = tmp_path / "r.mp4"
    reel.write_bytes(b"v")
    assert storage.upload_reel(reel, run_id="x", client_factory=FakeS3) is None


def test_upload_reel_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    assert storage.upload_reel(tmp_path / "nope.mp4", run_id="x", client_factory=FakeS3) is None


def test_upload_reel_uses_filename_override_for_key_basename(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"v")
    s3 = FakeS3()

    url = storage.upload_reel(
        reel, run_id="abc123", filename="hooks-20260714-abc123.mp4", client_factory=lambda: s3
    )

    assert url == (
        f"https://s3.example/{BUCKET}/outputs/abc123/hooks-20260714-abc123.mp4?X-Amz-Expires=86400"
    )
    assert s3.uploaded == [(str(reel), BUCKET, "outputs/abc123/hooks-20260714-abc123.mp4")]


def test_upload_reel_filename_none_preserves_path_name(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    reel = tmp_path / "reel01.mp4"
    reel.write_bytes(b"v")
    s3 = FakeS3()

    storage.upload_reel(reel, run_id="abc123", filename=None, client_factory=lambda: s3)

    assert s3.uploaded == [(str(reel), BUCKET, "outputs/abc123/reel01.mp4")]  # unchanged


def test_upload_reel_filename_cannot_escape_key_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"v")
    s3 = FakeS3()

    storage.upload_reel(reel, run_id="r", filename="../../evil.mp4", client_factory=lambda: s3)

    assert s3.uploaded[0][2] == "outputs/r/evil.mp4"  # basename only, no path escape
