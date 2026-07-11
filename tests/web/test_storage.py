"""Plan 3 — ObjectStorage media adapter (P0, ISC-46/47/48 + LEAFs).

Uses an injected fake S3 client (client_factory) so no boto3 / network is needed.
Mirrors tests/web/test_bucket_upload.py: org-scoped keys, presigned GET URLs,
fail-closed (503) when the bucket is unconfigured.
"""

from __future__ import annotations

import uuid

import pytest
from deps import (  # noqa: E402
    AppDeps,
    BadRequest,
    SchemaUnavailable,
    SlideRefResolverPort,
    StoragePort,
    default_deps,
)
from hypothesis import given
from hypothesis import strategies as st
from storage import ObjectStorage  # noqa: E402

BUCKET = "reel-media-test"


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.presign_calls: list = []

    def put_object(self, Bucket, Key, Body):  # noqa: N803 (boto3 kwarg names)
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.read()

    def head_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError  # boundary lib

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        assert op == "get_object"
        self.presign_calls.append((Params["Key"], ExpiresIn))
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


def _store(monkeypatch, s3, *, configured=True):
    if configured:
        monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    else:
        monkeypatch.delenv("REEL_BUCKET_NAME", raising=False)
    return ObjectStorage(client_factory=lambda: s3)


# ─────────────────────────── Behavior 1: stable org-scoped ref ───────────────────────────


def test_put_returns_org_scoped_stable_ref(monkeypatch):
    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    org = uuid.uuid4()
    ref = store.put(org, "carousel-1/slide-0.jpg", b"img-bytes")
    assert ref.startswith(f"{org}/")  # org-scoped
    assert store.exists(ref) is True  # round-trips
    assert store.put(org, "carousel-1/slide-0.jpg", b"img-bytes") == ref  # stable


def test_put_isolates_orgs_sharing_a_key(monkeypatch):
    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    a, b = uuid.uuid4(), uuid.uuid4()
    assert store.put(a, "c/s.jpg", b"x") != store.put(b, "c/s.jpg", b"x")


def test_put_accepts_file_like(monkeypatch):
    import io

    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    org = uuid.uuid4()
    ref = store.put(org, "c/s.jpg", io.BytesIO(b"stream-bytes"))
    assert store.exists(ref) is True


# Media keys have a domain (plan §"Property (input has a domain)"): path-like slugs
# produced by the pipeline (e.g. "carousel-1/slide-0.jpg"), not arbitrary text.
_MEDIA_KEYS = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_./"),
    min_size=1,
    max_size=40,
).filter(lambda k: k.lstrip("/") != "")


@given(org=st.uuids(), key=_MEDIA_KEYS)
def test_put_ref_is_always_org_prefixed_and_exists(org, key):
    # Property: for all (org, media-key), ref begins with "<org>/" and round-trips via
    # exists. An org never addresses another org's namespace.
    import os

    os.environ["REEL_BUCKET_NAME"] = BUCKET
    s3 = FakeS3()  # one backing store so put/exists share state across the round-trip
    store = ObjectStorage(client_factory=lambda: s3)
    ref = store.put(org, key, b"x")
    assert ref.startswith(f"{org}/")
    assert store.exists(ref) is True


# ─────────────────────────── Behavior 2: presigned_url time-bounded ───────────────────────────


def test_presigned_url_is_time_bounded(monkeypatch):
    s3 = FakeS3()
    store = _store(monkeypatch, s3)
    url = store.presigned_url("org/c/s.jpg", ttl=120)
    assert url.endswith("X-Amz-Expires=120")
    assert s3.presign_calls[-1] == ("org/c/s.jpg", 120)


def test_presigned_url_defaults_to_env_ttl(monkeypatch):
    monkeypatch.setenv("REEL_PRESIGN_TTL_S", "900")
    store = _store(monkeypatch, FakeS3())
    assert store.presigned_url("org/c/s.jpg").endswith("X-Amz-Expires=900")


def test_presigned_url_empty_ref_is_400(monkeypatch):
    store = _store(monkeypatch, FakeS3())
    with pytest.raises(BadRequest):
        store.presigned_url("   ")


# ─────────────────────────── Behavior 3: fail-closed 503 when unconfigured ───────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.put(uuid.uuid4(), "c/s.jpg", b"x"),
        lambda s: s.presigned_url("org/c/s.jpg"),
        lambda s: s.exists("org/c/s.jpg"),
    ],
)
def test_unconfigured_is_503(monkeypatch, call):
    s3 = FakeS3()
    store = _store(monkeypatch, s3, configured=False)
    with pytest.raises(SchemaUnavailable):
        call(store)
    assert s3.objects == {} and s3.presign_calls == []


# ─────────────────────────── Behavior 4: port seam + AppDeps wiring ───────────────────────────


def test_object_storage_satisfies_port():
    assert isinstance(ObjectStorage(), StoragePort)


def test_fake_storage_satisfies_port():
    from conftest import FakeSlideRefResolver, FakeStorage

    assert isinstance(FakeStorage(), StoragePort)
    assert isinstance(FakeSlideRefResolver(), SlideRefResolverPort)


def test_default_deps_wires_storage_no_io(monkeypatch):
    monkeypatch.setenv("REEL_BUCKET_NAME", "b")
    deps = default_deps()  # must not connect/network at build
    assert isinstance(deps, AppDeps)
    assert isinstance(deps.storage, StoragePort)
    assert deps.slides is not None  # fail-closed placeholder until Plan 6
