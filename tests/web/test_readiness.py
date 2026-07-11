"""B2 + fail-closed unblock: no schema / no session → 503/401/403, never a CP call.

The reel-af-ui consumes the SHARED, root-owned ``deepresearch`` schema; until it
is applied the service must deny and make no control-plane call. These tests pin
that contract at the resolver, the psycopg readiness gate, and the submit route.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import server
from auth import ResolverIdentity
from carousels import CarouselCreate
from conftest import FakeControlPlane, FakeIdentity, FakeReelJobRepo, make_deps
from deps import AuthContext, Forbidden, SchemaUnavailable, Unauthorized

TOPIC_URL = "/api/v1/execute/async/reel-af.reel_topic_to_reel"


class FakeSession:
    def __init__(self, uid: str, org: str | None = None, email: str | None = None):
        self._uid, self._org, self._email = uid, org, email

    def get_user_id(self) -> str:
        return self._uid

    def get_email(self):
        return self._email

    def get_active_org_id(self):
        return self._org


class FakeSessions:
    def __init__(self, session):
        self._s = session

    def get_session(self, _request):
        return self._s


class FakeReader:
    def __init__(self, resolved=None, error=None):
        self._resolved, self._error = resolved, error

    def ensure_ready(self):
        if self._error is not None:
            raise self._error

    def resolve_active(self, _st_id, _email, _claimed):
        return self._resolved


# ── resolver contract (plan §4) ──
def test_resolver_401_when_no_session():
    ident = ResolverIdentity(FakeSessions(None), FakeReader())
    with pytest.raises(Unauthorized):
        ident.resolve(object())


def test_resolver_503_when_schema_unavailable_before_resolution():
    reader = FakeReader(error=SchemaUnavailable("schema not applied"))
    ident = ResolverIdentity(FakeSessions(FakeSession("st-1")), reader)
    with pytest.raises(SchemaUnavailable):
        ident.resolve(object())


def test_resolver_403_when_membership_unresolved():
    ident = ResolverIdentity(FakeSessions(FakeSession("st-1")), FakeReader(resolved=None))
    with pytest.raises(Forbidden):
        ident.resolve(object())


def test_resolver_builds_context_from_reader():
    uid, oid = uuid.uuid4(), uuid.uuid4()
    reader = FakeReader(resolved=(uid, oid, "member"))
    ident = ResolverIdentity(FakeSessions(FakeSession("st-9")), reader)
    ctx = ident.resolve(object())
    assert ctx == AuthContext(user_id=uid, org_id=oid, role="member", supertokens_user_id="st-9")


# ── psycopg readiness gate fails closed with no DB URL (no driver needed) ──
def test_pg_repo_ensure_ready_is_503_without_database_url(monkeypatch):
    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    from pg import PgReelJobRepo

    with pytest.raises(SchemaUnavailable):
        PgReelJobRepo().ensure_ready()


def test_pg_reader_ensure_ready_is_503_without_database_url(monkeypatch):
    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    from pg import PgMembershipReader

    with pytest.raises(SchemaUnavailable):
        PgMembershipReader().ensure_ready()


def test_required_schema_includes_carousel_read_model():
    from pg import REQUIRED_SCHEMA

    assert REQUIRED_SCHEMA["carousel"] == {
        "id",
        "org_id",
        "created_by",
        "client_request_id",
        "status",
        "source_research_run_id",
        "hq_recreate_count",
        "execution_id",
        "created_at",
    }
    assert REQUIRED_SCHEMA["carousel_slide"] == {
        "carousel_id",
        "org_id",
        "idx",
        "image_ref",
        "prompt",
        "status",
    }


def test_pg_carousel_repo_methods_fail_closed_without_database_url(monkeypatch):
    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    from pg import PgCarouselRepo

    repo = PgCarouselRepo()
    ctx = AuthContext(
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        role="member",
        supertokens_user_id="st-x",
    )

    with pytest.raises(SchemaUnavailable):
        repo.insert_or_get_draft(
            ctx,
            CarouselCreate(source_text="doc", preset="carousel-default"),
            uuid.uuid4(),
            datetime.now(timezone.utc),
            "K",
        )


# ── submit route fails closed on schema-unavailable: 503, no row, no CP (B2) ──
def test_submit_is_503_when_schema_unavailable():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(
        identity=FakeIdentity(error=SchemaUnavailable("schema not applied")),
        reel_jobs=repo,
        control_plane=cp,
    )
    client = server.create_app(deps, enable_supertokens=False).test_client()
    resp = client.post(TOPIC_URL, json={"input": {"topic": "black holes"}})
    assert resp.status_code == 503
    assert repo.inserted == [] and cp.dispatch_calls == []


# ── default_deps() is import-safe and fail-closed (no session → 401, no I/O) ──
def test_default_deps_identity_is_fail_closed():
    from deps import default_deps

    deps = default_deps()
    with pytest.raises(Unauthorized):
        deps.identity.resolve(object())
