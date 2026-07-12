"""Integration (INT-01 · B4): reel-af provenance is keyed by ``execution_id`` and read
back NON-NULL by ``PgReelJobRepo.get_by_execution`` against a live Postgres.

This is the seam §5 regression: removing reel-af's owner-table writes (Phase 0) must not
regress the reader — a provenance row stamped with ``source_research_run_id`` must survive
the ``execution_id`` round-trip and never come back NULL, and a foreign org must be
concealed (404/``NotFound``). The existing ``test_pg_reel_jobs`` covers execution scoping
with a NULL provenance; this module is the non-null-provenance readback it does not exercise.

Runs ONLY when ``TEST_DATABASE_URL`` points at a live Postgres; otherwise skipped with an
explicit reason (the fail-closed signal — a skip is never a false green). Fixtures live in
this module to avoid the pytest ``conftest`` module-name collision with ``tests/web/conftest.py``.
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
NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)

_SCHEMA = """
drop schema if exists deepresearch cascade;
create schema deepresearch;
create table deepresearch.organization (
    id uuid primary key, slug text unique not null, name text not null,
    status text not null default 'active'
);
create table deepresearch.user (
    id uuid primary key, supertokens_user_id text unique not null, email text,
    status text not null default 'active'
);
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
create index if not exists reel_job_execution_idx on deepresearch.reel_job (execution_id);
"""


@pytest.fixture()
def db(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — live Postgres required (fail-closed: unverified)")
    import psycopg

    monkeypatch.setenv("DEEPRESEARCH_DATABASE_URL", TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(_SCHEMA)
    return TEST_DATABASE_URL


@pytest.fixture()
def seed(db):
    """One org + one user; return (org_id, user_id)."""
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
    return org_id, user_id


def _ctx(org_id, user_id, role="member"):
    return AuthContext(user_id=user_id, org_id=org_id, role=role, supertokens_user_id="st-x")


def _seed_research_run(db, org_id, user_id):
    """Insert an owner research_run row and return its id (the provenance target)."""
    import psycopg

    rid = uuid.uuid4()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.research_run(id, org_id, created_by, execution_id, status) "
            "values (%s,%s,%s,%s,'succeeded')",
            (rid, org_id, user_id, "exec_src_" + rid.hex),
        )
    return rid


def _submission(source_research_run_id):
    return ReelSubmission(
        target="reel-af.reel_topic_to_reel", title="black holes", source_url=None,
        topic="black holes", source_research_run_id=source_research_run_id,
        params={"topic": "black holes", "target": "reel-af.reel_topic_to_reel"},
        cp_input={"topic": "black holes"},
    )


def test_provenance_read_back_by_execution_id_nonnull(db, seed):
    org_id, user_id = seed
    repo, ctx = PgReelJobRepo(), _ctx(*seed)
    prov = _seed_research_run(db, org_id, user_id)          # the research-run correlation ref

    job = repo.insert_or_get_queued(ctx, _submission(prov), uuid.uuid4(), NOW, "KEY-PROV")
    exec_id = "exec_" + uuid.uuid4().hex
    repo.attach_execution_id(ctx, job.job_id, exec_id)

    ref = repo.get_by_execution(ctx, exec_id)               # joins by execution_id, org-scoped
    assert ref.execution_id == exec_id
    assert ref.source_research_run_id == prov               # provenance NOT null on read (seam §5)


def test_cross_org_execution_id_conceals(db, seed):
    org_id, user_id = seed
    repo, ctx = PgReelJobRepo(), _ctx(*seed)
    prov = _seed_research_run(db, org_id, user_id)

    job = repo.insert_or_get_queued(ctx, _submission(prov), uuid.uuid4(), NOW, "KEY-XORG")
    exec_id = "exec_" + uuid.uuid4().hex
    repo.attach_execution_id(ctx, job.job_id, exec_id)

    foreign = _ctx(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(NotFound):
        repo.get_by_execution(foreign, exec_id)             # foreign org -> 404-conceal
