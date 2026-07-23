"""Integration: PgReelJobRepo SQL contract against a live Postgres.

Runs ONLY when ``TEST_DATABASE_URL`` points at a live Postgres; otherwise skipped.
The schema below is a **test fixture**, not a vendored migration — the production
schema is root-owned (`migrations/deepresearch/`). It mirrors the columns/keys/FKs
the adapter depends on so the SQL contract (readiness, org-scoping, idempotency
uniqueness, monotonic reconcile) is provable — what fakes can't show.

Fixtures live in this module (not a sibling ``conftest.py``) to avoid the
pytest ``conftest`` module-name collision with ``tests/web/conftest.py``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from deps import AuthContext, NotFound, SchemaUnavailable
from pg import PgMembershipReader, PgReelJobRepo
from reel_jobs import ReelSubmission

DEFAULT_ORG = "e4e47131-cd9f-4882-9925-194e9db062ca"

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

_SCHEMA = """
drop schema if exists deepresearch cascade;
create schema deepresearch;
create table deepresearch.organization (
    id uuid primary key,
    slug text unique not null,
    name text not null,
    status text not null default 'active'
);
create table deepresearch.user (
    id uuid primary key,
    supertokens_user_id text unique not null,
    email text,
    status text not null default 'active'
);
create table deepresearch.membership (
    org_id uuid not null references deepresearch.organization(id),
    user_id uuid not null references deepresearch.user(id),
    role text not null,
    status text not null default 'active',
    primary key (org_id, user_id)
);
create table deepresearch.role_definition (role text primary key, permissions jsonb not null);
create table deepresearch.research_run (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null references deepresearch.user(id),
    execution_id text,
    status text not null default 'queued',
    created_at timestamptz not null default now()
);
create table deepresearch.project (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null,
    name text not null,
    description text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);
create table deepresearch.reel_job (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null references deepresearch.user(id),
    client_request_id text not null,
    title text,
    source_url text,
    topic text,
    source_research_run_id uuid references deepresearch.research_run(id) on delete set null,
    project_id uuid references deepresearch.project(id) on delete set null,
    params jsonb not null default '{}',
    status text not null default 'queued',
    result_ref text,
    execution_id text,
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    unique (org_id, created_by, client_request_id)
);
create table deepresearch.carousel (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null references deepresearch.user(id),
    client_request_id text not null,
    status text not null default 'draft',
    source_research_run_id uuid references deepresearch.research_run(id) on delete set null,
    hq_recreate_count integer not null default 0,
    execution_id text,
    created_at timestamptz not null default now(),
    unique (org_id, created_by, client_request_id)
);
create table deepresearch.carousel_slide (
    carousel_id uuid not null references deepresearch.carousel(id) on delete cascade,
    org_id uuid not null,
    idx integer not null,
    image_ref text,
    prompt text,
    status text not null default 'ok',
    primary key (carousel_id, idx)
);
"""


@pytest.fixture()
def db(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — live Postgres required")
    import psycopg

    monkeypatch.setenv("DEEPRESEARCH_DATABASE_URL", TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(_SCHEMA)
    return TEST_DATABASE_URL


@pytest.fixture()
def seed(db):
    """Insert one org + one active member user; return (org_id, user_id)."""
    import psycopg

    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.organization(id, slug, name) values (%s,%s,'Test Org')",
            (org_id, "org-" + org_id.hex),
        )
        conn.execute(
            "insert into deepresearch.user(id, supertokens_user_id) values (%s,%s)",
            (user_id, "st-" + user_id.hex),
        )
        conn.execute(
            "insert into deepresearch.membership(org_id, user_id, role) values (%s,%s,'member')",
            (org_id, user_id),
        )
    return org_id, user_id


def _ctx(org_id, user_id, role="member"):
    return AuthContext(user_id=user_id, org_id=org_id, role=role, supertokens_user_id="st-x")


def _submission():
    return ReelSubmission(
        target="reel-af.reel_topic_to_reel", title="black holes", source_url=None,
        topic="black holes", source_research_run_id=None,
        params={"topic": "black holes", "target": "reel-af.reel_topic_to_reel"},
        cp_input={"topic": "black holes"},
    )


@pytest.fixture()
def seeded_org(db, monkeypatch):
    """Seed the default org + role_definitions (no users) for JIT-bootstrap tests."""
    import psycopg

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.organization(id, slug, name) values (%s,'silmari-default','D')",
            (DEFAULT_ORG,),
        )
        conn.execute(
            "insert into deepresearch.role_definition(role, permissions) values "
            "('owner','{}'), ('admin','{}'), ('member','{}'), ('viewer','{}')"
        )
    monkeypatch.setenv("REEL_DEFAULT_ORG_ID", DEFAULT_ORG)
    monkeypatch.setenv("REEL_OWNER_EMAILS", "maceo.jourdan@gmail.com")
    return DEFAULT_ORG


def test_jit_bootstrap_creates_owner_then_member(seeded_org):
    reader = PgMembershipReader()

    owner = reader.resolve_active("st-owner", "maceo.jourdan@gmail.com", None)
    assert owner is not None
    owner_uid, org_id, role = owner
    assert str(org_id) == DEFAULT_ORG and role == "owner"

    # idempotent: second login for same SuperTokens id returns the same user, no dup
    again = reader.resolve_active("st-owner", "maceo.jourdan@gmail.com", None)
    assert again[0] == owner_uid

    # a non-owner email bootstraps as member
    member = reader.resolve_active("st-guest", "guest@example.com", None)
    assert member is not None and member[2] == "member"


def test_ensure_ready_is_green_when_schema_applied(seed):
    PgReelJobRepo().ensure_ready()  # must not raise once the fixture schema exists


def test_ensure_ready_503_when_schema_missing(db):
    import psycopg

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("drop schema deepresearch cascade")
    with pytest.raises(SchemaUnavailable):
        PgReelJobRepo().ensure_ready()


def test_insert_stamps_owner_and_is_idempotent(seed):
    org_id, user_id = seed
    repo, ctx = PgReelJobRepo(), _ctx(*seed)

    first = repo.insert_or_get_queued(ctx, _submission(), uuid.uuid4(), NOW, "KEY-1")
    assert first.created is True and first.org_id == org_id and first.created_by == user_id

    # same idempotency key → returns existing row, created=False, no duplicate
    second = repo.insert_or_get_queued(ctx, _submission(), uuid.uuid4(), NOW, "KEY-1")
    assert second.created is False and second.job_id == first.job_id


def test_execution_scoping_and_reconcile(seed):
    repo, ctx = PgReelJobRepo(), _ctx(*seed)
    job = repo.insert_or_get_queued(ctx, _submission(), uuid.uuid4(), NOW, "KEY-2")
    repo.attach_execution_id(ctx, job.job_id, "exec_abc")

    got = repo.get_by_execution(ctx, "exec_abc")
    assert got.job_id == job.job_id and got.execution_id == "exec_abc"

    # foreign org cannot see it
    foreign = _ctx(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(NotFound):
        repo.get_by_execution(foreign, "exec_abc")

    # reconcile to succeeded, then a stale 'producing' poll must NOT downgrade it
    repo.update_from_execution(ctx, "exec_abc", "succeeded", "cp-execution://exec_abc/result", NOW)
    repo.update_from_execution(ctx, "exec_abc", "producing", None, None)
    final = repo.get_by_execution(ctx, "exec_abc")
    assert final.status == "succeeded"
    assert final.result_ref == "cp-execution://exec_abc/result"
