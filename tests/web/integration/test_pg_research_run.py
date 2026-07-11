"""Integration: research_run provenance SQL contract.

Runs only when ``TEST_DATABASE_URL`` points at live Postgres; otherwise the
``db`` fixture skips. The schema is a test fixture, not a vendored migration.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from deps import AuthContext, NotFound
from pg import PgReelJobRepo
from reel_jobs import ReelSubmission

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
OTHER_ORG = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_USER = uuid.UUID("44444444-4444-4444-4444-444444444444")

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
create table deepresearch.reel_job (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null references deepresearch.user(id),
    client_request_id text not null,
    title text,
    source_url text,
    topic text,
    source_research_run_id uuid references deepresearch.research_run(id) on delete set null,
    params jsonb not null default '{}',
    status text not null default 'queued',
    result_ref text,
    execution_id text,
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    unique (org_id, created_by, client_request_id)
);
"""


@pytest.fixture()
def db(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set - live Postgres required")
    import psycopg

    monkeypatch.setenv("DEEPRESEARCH_DATABASE_URL", TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(_SCHEMA)
    return TEST_DATABASE_URL


@pytest.fixture()
def seed(db):
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


def _submission(source_research_run_id=None):
    return ReelSubmission(
        target="reel-af.reel_topic_to_reel",
        title="black holes",
        source_url=None,
        topic="black holes",
        source_research_run_id=source_research_run_id,
        params={"topic": "black holes", "target": "reel-af.reel_topic_to_reel"},
        cp_input={"topic": "black holes"},
    )


def test_insert_research_run_is_owner_scoped_and_readable(seed, db):
    org_id, user_id = seed
    repo, ctx = PgReelJobRepo(), _ctx(org_id, user_id)
    rid = uuid.uuid4()

    repo.insert_research_run(ctx, rid, None, "queued", NOW)
    repo.update_research_status(ctx, rid, execution_id="exec_r9")

    got = repo.get_research_run(ctx, rid)
    assert got.org_id == org_id
    assert got.created_by == user_id
    assert got.execution_id == "exec_r9"
    assert repo.get_research_by_execution(ctx, "exec_r9").id == rid
    with pytest.raises(NotFound):
        repo.get_research_run(_ctx(OTHER_ORG, OTHER_USER), rid)


def test_update_research_status_is_terminal_monotonic(seed, db):
    repo, ctx = PgReelJobRepo(), _ctx(*seed)
    rid = uuid.uuid4()

    repo.insert_research_run(ctx, rid, "exec_t", "succeeded", NOW)
    repo.update_research_status(ctx, rid, status="running")

    assert repo.get_research_run(ctx, rid).status == "succeeded"


def test_stamped_source_research_run_id_is_read_back(seed, db):
    repo, ctx = PgReelJobRepo(), _ctx(*seed)
    rid = uuid.uuid4()

    repo.insert_research_run(ctx, rid, "exec_seed", "succeeded", NOW)
    job_id = uuid.uuid4()
    repo.insert_or_get_queued(ctx, _submission(source_research_run_id=rid), job_id, NOW, "K-prov")
    repo.attach_execution_id(ctx, job_id, "exec_c1")

    read = repo.get_by_execution(ctx, "exec_c1")
    assert read.source_research_run_id == rid
