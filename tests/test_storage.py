"""T10: storage.upload_reel — deliver produced reels out to the shared bucket.

Injected fake S3 client (no boto3 / network). Fail-soft when unconfigured or missing.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import pytest

from reel_af import storage
from reel_af.app import _resolve_artifact_ref

BUCKET = "reel-uploads-test"
CORE_KEYS = [
    "plans/abc123/composite.ts.md",
    "plans/abc123/transcript.words.json",
    "plans/abc123/hook-plan.json",
]
SIDECAR_REF_KEYS = {
    "mined_candidates_ref",
    "accepted_candidates_ref",
    "strategy_ref",
    "blueprint_ref",
    "script_coherence_ref",
}


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


class FakeA1S3:
    def __init__(
        self,
        *,
        fail_put_key: str | None = None,
        fail_presign_key: str | None = None,
        url_by_key: dict[str, str] | None = None,
    ):
        self.fail_put_key = fail_put_key
        self.fail_presign_key = fail_presign_key
        self.url_by_key = url_by_key or {}
        self.puts: list[dict] = []
        self.presigned: list[dict] = []
        self.bodies_by_key: dict[str, bytes] = {}
        self.bodies_by_url: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803 (boto3 kwarg)
        if Key == self.fail_put_key:
            raise OSError("put_object failed")
        body = bytes(Body)
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": body})
        self.bodies_by_key[Key] = body

    def generate_presigned_url(self, operation, Params, ExpiresIn):  # noqa: N803
        assert operation == "get_object"
        key = Params["Key"]
        if key == self.fail_presign_key:
            raise OSError("presign failed")
        self.presigned.append(
            {"Bucket": Params["Bucket"], "Key": key, "ExpiresIn": ExpiresIn}
        )
        url = self.url_by_key.get(
            key,
            f"https://s3.example/{Params['Bucket']}/{key}?X-Amz-Expires={ExpiresIn}",
        )
        self.bodies_by_url[url] = self.bodies_by_key[key]
        return url


def _seed_a1_result(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    composite = tmp_path / "composite.ts.md"
    words = tmp_path / "transcript.words.json"
    hook = tmp_path / "hook-plan.json"
    composite.write_text("00:00:04.120  They don't reason.\n", encoding="utf-8")
    words.write_text('{"schema_version":"1","words":[]}', encoding="utf-8")
    hook.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "clips": [
                    {
                        "idx": 1,
                        "composite_ref": str(composite),
                        "idempotency_key": "immutable-local-ref-derived-key",
                        "hook": "They don't reason",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = {
        "composite_ref": str(composite),
        "words_ref": str(words),
        "hook_ref": str(hook),
        "clip_count": 1,
    }
    for key in SIDECAR_REF_KEYS:
        sidecar = tmp_path / f"{key}.json"
        sidecar.write_text('{"debug":true}', encoding="utf-8")
        result[key] = str(sidecar)
    return result, {"composite": composite, "words": words, "hook": hook}


def _seed_multi_clip_a1_result(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    composite1 = tmp_path / "composite.ts.md"
    clip2_dir = tmp_path / "clips" / "clip-002"
    composite2 = clip2_dir / "composite.ts.md"
    words = tmp_path / "transcript.words.json"
    hook = tmp_path / "hook-plan.json"
    clip2_dir.mkdir(parents=True)
    composite1.write_text("00:00:04.120  They don't reason.\n", encoding="utf-8")
    composite2.write_text("00:01:12.300  So the fix is a tighter loop.\n", encoding="utf-8")
    words.write_text('{"schema_version":"1","words":[]}', encoding="utf-8")
    hook.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "clips": [
                    {
                        "idx": 1,
                        "composite_ref": str(composite1),
                        "idempotency_key": "immutable-key-1",
                    },
                    {
                        "idx": 2,
                        "composite_ref": str(composite2),
                        "idempotency_key": "immutable-key-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "composite_ref": str(composite1),
        "words_ref": str(words),
        "hook_ref": str(hook),
        "clip_count": 2,
    }, {"composite1": composite1, "composite2": composite2, "words": words, "hook": hook}


def _assert_hosted(ref: str) -> None:
    parsed = urlparse(ref)
    assert parsed.scheme in {"http", "https"}
    assert parsed.netloc


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


def test_publish_a1_artifacts_uploads_core_with_fixed_keys_and_rewrites_hook(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, paths = _seed_a1_result(tmp_path)
    original = dict(result)
    s3 = FakeA1S3()

    out = storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert out is not result
    assert result == original
    assert [put["Key"] for put in s3.puts] == CORE_KEYS
    assert s3.puts[0] == {
        "Bucket": BUCKET,
        "Key": "plans/abc123/composite.ts.md",
        "Body": paths["composite"].read_bytes(),
    }
    assert s3.puts[1] == {
        "Bucket": BUCKET,
        "Key": "plans/abc123/transcript.words.json",
        "Body": paths["words"].read_bytes(),
    }
    for field in ("composite_ref", "words_ref", "hook_ref"):
        _assert_hosted(out[field])
    assert out["clip_count"] == 1
    assert not (SIDECAR_REF_KEYS & set(out))
    assert str(tmp_path) not in json.dumps(out)

    uploaded_hook = json.loads(s3.bodies_by_key["plans/abc123/hook-plan.json"].decode())
    clip = uploaded_hook["clips"][0]
    assert clip["composite_ref"] == out["composite_ref"]
    assert clip["idempotency_key"] == "immutable-local-ref-derived-key"
    assert str(paths["composite"]) not in json.dumps(uploaded_hook)
    assert out["hook_ref"] == (
        f"https://s3.example/{BUCKET}/plans/abc123/hook-plan.json?X-Amz-Expires=86400"
    )


def test_publish_a1_artifacts_uploads_and_rewrites_multi_clip_composites(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, paths = _seed_multi_clip_a1_result(tmp_path)
    s3 = FakeA1S3()

    out = storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert [put["Key"] for put in s3.puts] == [
        "plans/abc123/composite.ts.md",
        "plans/abc123/clips/clip-002/composite.ts.md",
        "plans/abc123/transcript.words.json",
        "plans/abc123/hook-plan.json",
    ]
    assert s3.bodies_by_key["plans/abc123/composite.ts.md"] == paths["composite1"].read_bytes()
    assert (
        s3.bodies_by_key["plans/abc123/clips/clip-002/composite.ts.md"]
        == paths["composite2"].read_bytes()
    )
    assert out["clip_count"] == 2
    assert out["composite_ref"] == (
        f"https://s3.example/{BUCKET}/plans/abc123/composite.ts.md?X-Amz-Expires=86400"
    )
    assert _assert_hosted(out["words_ref"]) is None
    assert _assert_hosted(out["hook_ref"]) is None

    uploaded_hook = json.loads(s3.bodies_by_key["plans/abc123/hook-plan.json"].decode())
    clips = uploaded_hook["clips"]
    assert [clip["idempotency_key"] for clip in clips] == [
        "immutable-key-1",
        "immutable-key-2",
    ]
    assert [clip["composite_ref"] for clip in clips] == [
        out["composite_ref"],
        f"https://s3.example/{BUCKET}/plans/abc123/clips/clip-002/composite.ts.md?X-Amz-Expires=86400",
    ]
    assert clips[0]["composite_ref"] != clips[1]["composite_ref"]
    assert str(tmp_path) not in json.dumps(out)
    assert str(tmp_path) not in json.dumps(uploaded_hook)


def test_publish_a1_artifacts_no_bucket_preserves_local_refs_without_client(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("REEL_BUCKET_NAME", raising=False)
    result, _paths = _seed_a1_result(tmp_path)

    def fail_client():
        raise AssertionError("S3 client must not be constructed without a bucket")

    out = storage.publish_a1_artifacts(result, run_id="abc123", client_factory=fail_client)

    assert out == result
    assert out is not result
    assert SIDECAR_REF_KEYS <= set(out)


@pytest.mark.parametrize("field", ["composite_ref", "words_ref", "hook_ref"])
def test_publish_a1_artifacts_missing_core_file_raises_without_local_path(
    tmp_path, monkeypatch, field
):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, _paths = _seed_a1_result(tmp_path)
    result[field] = str(tmp_path / "missing-artifact")
    s3 = FakeA1S3()

    with pytest.raises(Exception) as excinfo:
        storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert field in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)
    assert s3.puts == []


def test_publish_a1_artifacts_upload_failure_raises_in_bucket_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, _paths = _seed_a1_result(tmp_path)
    s3 = FakeA1S3(fail_put_key="plans/abc123/transcript.words.json")

    with pytest.raises(Exception) as excinfo:
        storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert "put_object failed" in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)
    assert [put["Key"] for put in s3.puts] == ["plans/abc123/composite.ts.md"]


def test_publish_a1_artifacts_presign_failure_raises_in_bucket_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, _paths = _seed_a1_result(tmp_path)
    s3 = FakeA1S3(fail_presign_key="plans/abc123/composite.ts.md")

    with pytest.raises(Exception) as excinfo:
        storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert "presign failed" in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)


def test_publish_a1_artifacts_malformed_presign_url_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, _paths = _seed_a1_result(tmp_path)
    s3 = FakeA1S3(url_by_key={"plans/abc123/composite.ts.md": "https://"})

    with pytest.raises(Exception) as excinfo:
        storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert "composite_ref" in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)


@pytest.mark.parametrize(
    ("delivery_ttl", "artifact_ttl", "expected"),
    [(None, None, 86400), ("240", None, 240), ("240", "900", 900)],
)
def test_publish_a1_artifacts_uses_named_artifact_ttl_policy(
    tmp_path, monkeypatch, delivery_ttl, artifact_ttl, expected
):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    if delivery_ttl is None:
        monkeypatch.delenv("REEL_DELIVERY_TTL_S", raising=False)
    else:
        monkeypatch.setenv("REEL_DELIVERY_TTL_S", delivery_ttl)
    if artifact_ttl is None:
        monkeypatch.delenv("REEL_ARTIFACT_TTL_S", raising=False)
    else:
        monkeypatch.setenv("REEL_ARTIFACT_TTL_S", artifact_ttl)
    result, _paths = _seed_a1_result(tmp_path)
    s3 = FakeA1S3()

    storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)

    assert {call["ExpiresIn"] for call in s3.presigned} == {expected}


def test_published_a1_refs_resolve_through_artifact_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    result, _paths = _seed_a1_result(tmp_path / "producer")
    s3 = FakeA1S3()
    published = storage.publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: s3)
    dest = tmp_path / "consumer"
    dest.mkdir()

    for field, filename, key in [
        ("composite_ref", "composite.ts.md", "plans/abc123/composite.ts.md"),
        ("words_ref", "transcript.words.json", "plans/abc123/transcript.words.json"),
        ("hook_ref", "hook-plan.json", "plans/abc123/hook-plan.json"),
    ]:
        resolved = _resolve_artifact_ref(
            published[field], dest, filename, lambda url: s3.bodies_by_url[url]
        )
        assert resolved == dest / filename
        assert resolved.read_bytes() == s3.bodies_by_key[key]
