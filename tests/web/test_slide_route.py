"""Plan 3 Behavior 5 — authed, org-scoped image-serving route (BLOCKING closure).

GET /api/v1/carousels/{cid}/slides/{idx}:
- ISC-46: owner fetch → 302 to a presigned object-storage URL.
- ISC-47: cross-org → 404 (concealed), no presigned URL minted.
- ISC-48: missing/expired ref → 404 + clear code, never 500.
Auth-before-work: no session → 401 before any resolve/storage call.

The closure test drives the real chain through the mounted HTTP entrypoint with a
real ObjectStorage (fake S3 client_factory at the true boundary) and asserts the
user-visible 302 Location; the red-at-seam proof disables the route registration.
"""

from __future__ import annotations

import uuid

import server
from conftest import (
    ORG_ID,
    FakeIdentity,
    FakeSlideRefResolver,
    FakeStorage,
    make_ctx,
    make_deps,
)
from deps import Unauthorized

CID = "car_1"


def _url(idx=0):
    return f"/api/v1/carousels/{CID}/slides/{idx}"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


# ─────────────────────────── unit (Tier-2 support) ───────────────────────────


def test_owner_fetch_redirects_to_presigned_url():  # ISC-46
    storage = FakeStorage(objects={"ref-0": b"img"})
    slides = FakeSlideRefResolver({(ORG_ID, CID, 0): "ref-0"})
    deps = make_deps(identity=FakeIdentity(make_ctx()), storage=storage, slides=slides)
    resp = _client(deps).get(_url(0))
    assert resp.status_code == 302
    assert resp.headers["Location"] == storage.presigned_for("ref-0")


def test_cross_org_fetch_is_404_and_mints_no_url():  # ISC-47
    storage = FakeStorage(objects={"ref-0": b"img"})
    slides = FakeSlideRefResolver({(ORG_ID, CID, 0): "ref-0"})  # owned by ORG_ID
    other = make_ctx()
    object.__setattr__(other, "org_id", uuid.uuid4())
    deps = make_deps(identity=FakeIdentity(other), storage=storage, slides=slides)
    assert _client(deps).get(_url(0)).status_code == 404
    assert storage.presign_calls == []  # concealed, nothing minted


def test_missing_or_expired_ref_is_404_not_500():  # ISC-48
    storage = FakeStorage(objects={})  # ref resolves but object gone
    slides = FakeSlideRefResolver({(ORG_ID, CID, 0): "ref-gone"})
    deps = make_deps(identity=FakeIdentity(make_ctx()), storage=storage, slides=slides)
    resp = _client(deps).get(_url(0))
    assert resp.status_code == 404
    assert resp.get_json()["code"]  # clear error code, not a bare 500


def test_no_session_is_401_before_storage():
    storage = FakeStorage(objects={"ref-0": b"img"})
    deps = make_deps(
        identity=FakeIdentity(error=Unauthorized("no session")),
        storage=storage,
        slides=FakeSlideRefResolver({(ORG_ID, CID, 0): "ref-0"}),
    )
    assert _client(deps).get(_url(0)).status_code == 401
    assert storage.presign_calls == []


def test_non_integer_idx_is_404():
    # A non-integer <idx> does not match _SLIDE_RE → unknown route → _not_found.
    deps = make_deps(identity=FakeIdentity(make_ctx()))
    assert _client(deps).get(f"/api/v1/carousels/{CID}/slides/abc").status_code == 404


# ─────────────────────────── closure (BLOCKING) ───────────────────────────


class _ClosureS3:
    """boto3-shaped S3 double — the one true boundary in the closure test."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.presign_calls: list = []

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self.objects[(Bucket, Key)] = Body

    def head_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        self.presign_calls.append((Params["Key"], ExpiresIn))
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


def _real_storage_deps(monkeypatch, s3, ref):
    """Real ObjectStorage (fake S3 at the boundary) + a seeded slide resolver."""
    from storage import ObjectStorage

    monkeypatch.setenv("REEL_BUCKET_NAME", "reel-media-closure")
    org = make_ctx()
    storage = ObjectStorage(client_factory=lambda: s3)
    storage.put(org.org_id, "slide-0.jpg", b"real-bytes")  # seeds s3 under <org>/slide-0.jpg
    slides = FakeSlideRefResolver({(org.org_id, CID, 0): ref})
    return make_deps(identity=FakeIdentity(org), storage=storage, slides=slides), storage


def test_closure_owner_browser_fetches_slide_via_real_presign(monkeypatch):
    # SOURCE seeded; TRIGGER = mounted HTTP route; OBSERVE = the 302 Location produced
    # by the REAL ObjectStorage.presigned_url (the production read path the browser follows).
    s3 = _ClosureS3()
    ref = f"{make_ctx().org_id}/slide-0.jpg"
    deps, _storage = _real_storage_deps(monkeypatch, s3, ref)
    resp = _client(deps).get(_url(0))
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert loc.startswith("https://s3.example/reel-media-closure/") and ref in loc
    assert s3.presign_calls and s3.presign_calls[-1][0] == ref


def test_closure_red_at_seam(monkeypatch):
    # RED-AT-SEAM: disable the route registration (predicate returns None → _not_found);
    # the owner-fetch 302 assertion goes red. Re-enabled (normal) → green above.
    s3 = _ClosureS3()
    ref = f"{make_ctx().org_id}/slide-0.jpg"
    deps, _storage = _real_storage_deps(monkeypatch, s3, ref)
    monkeypatch.setattr(server, "_slide_target", lambda _m, _s: None)  # seam removed
    resp = _client(deps).get(_url(0))
    assert resp.status_code == 404  # falls to _not_found without the branch
    assert s3.presign_calls == []  # nothing minted when the seam is gone
