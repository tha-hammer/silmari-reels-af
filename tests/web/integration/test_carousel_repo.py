"""Integration: PgCarouselRepo SQL contract against a live Postgres.

Runs only when ``TEST_DATABASE_URL`` points at live Postgres; otherwise the
``db`` fixture skips. The schema is a test fixture, not a vendored migration.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from carousels import CarouselCreate, HqRecreateCapError
from deps import AuthContext, NotFound
from pg import PgCarouselRepo

from reel_af.recreate import HQ_RECREATE_CAP

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


def _create():
    return CarouselCreate(source_text="doc", preset="carousel-default")


def _persist_slides(db, org_id, carousel_id):
    import psycopg

    with psycopg.connect(db, autocommit=True) as conn:
        for idx in (0, 1, 2):
            conn.execute(
                "insert into deepresearch.carousel_slide "
                "(carousel_id, org_id, idx, image_ref, prompt, status) "
                "values (%s,%s,%s,%s,%s,'ok')",
                (carousel_id, org_id, idx, f"ref-{idx}", f"p{idx}"),
            )


def test_insert_get_replace_and_status_are_org_scoped(seed, db):
    org_id, user_id = seed
    repo, ctx = PgCarouselRepo(), _ctx(org_id, user_id)
    carousel_id = uuid.uuid4()

    first = repo.insert_or_get_draft(ctx, _create(), carousel_id, NOW, "K-car")
    second = repo.insert_or_get_draft(ctx, _create(), uuid.uuid4(), NOW, "K-car")

    assert first.created is True
    assert second.created is False and second.job_id == carousel_id

    repo.attach_execution_id(ctx, carousel_id, "exec_car")
    _persist_slides(db, org_id, carousel_id)

    view = repo.get(ctx, carousel_id)
    assert [slide["idx"] for slide in view.slides] == [0, 1, 2]
    assert repo.slide_ref(ctx, carousel_id, 1) == "ref-1"

    repo.replace_slide(ctx, carousel_id, 1, "ref-1-new", "new prompt", "ok")
    assert repo.slide_ref(ctx, carousel_id, 1) == "ref-1-new"

    repo.set_status(ctx, carousel_id, "succeeded")
    repo.set_status(ctx, carousel_id, "producing")
    assert repo.get(ctx, carousel_id).status == "succeeded"

    foreign = _ctx(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(NotFound):
        repo.get(foreign, carousel_id)
    with pytest.raises(NotFound):
        repo.replace_slide(foreign, carousel_id, 1, "x", "x", "ok")


def test_hq_recreate_register_is_capped(seed, db):
    repo, ctx = PgCarouselRepo(), _ctx(*seed)
    carousel_id = uuid.uuid4()
    repo.insert_or_get_draft(ctx, _create(), carousel_id, NOW, "K-hq")

    for expected in range(1, HQ_RECREATE_CAP + 1):
        assert repo.register_hq_recreate(ctx, carousel_id) == expected
    with pytest.raises(HqRecreateCapError):
        repo.register_hq_recreate(ctx, carousel_id)
