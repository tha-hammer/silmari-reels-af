"""Integration: project / project_asset SQL constraint contract (AF-4pz.3).

Runs ONLY when ``TEST_DATABASE_URL`` points at a live Postgres; otherwise
skipped. The schema below is a **test fixture** mirroring the root-owned
migration `migrations/deepresearch/115_create_project.sql` — it proves the
constraint contract (exactly-one-of asset refs, link⇔url, cascades,
reel_job.project_id set-null) that fakes can't show.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

_SCHEMA = """
drop schema if exists deepresearch cascade;
create schema deepresearch;
create table deepresearch.organization (
    id uuid primary key,
    slug text unique not null,
    name text not null,
    status text not null default 'active'
);
create table deepresearch.source_asset (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null,
    bucket_key text not null,
    original_filename text not null,
    content_type text,
    size_bytes bigint not null,
    checksum text,
    status text not null default 'stored',
    created_at timestamptz not null default now(),
    deleted_at timestamptz
);
-- Mirrors migration 115 (no created_by user FK per 113 / AF-ggp).
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
create table deepresearch.project_asset (
    id uuid primary key,
    project_id uuid not null references deepresearch.project(id) on delete cascade,
    org_id uuid not null references deepresearch.organization(id),
    asset_type text not null
        constraint project_asset_type_check
        check (asset_type in ('video','image','link','document')),
    source_asset_id uuid references deepresearch.source_asset(id) on delete set null,
    bucket_key text,
    url text,
    title text,
    created_at timestamptz not null default now(),
    deleted_at timestamptz,
    constraint project_asset_exactly_one_ref check (
        (source_asset_id is not null)::int
        + (bucket_key is not null)::int
        + (url is not null)::int = 1
    ),
    constraint project_asset_ref_matches_type check (
        (asset_type = 'link' and url is not null)
        or (asset_type in ('video','image','document') and url is null)
    )
);
create table deepresearch.reel_job (
    id uuid primary key,
    org_id uuid not null references deepresearch.organization(id),
    created_by uuid not null,
    client_request_id text not null,
    status text not null default 'queued',
    project_id uuid references deepresearch.project(id) on delete set null,
    created_at timestamptz not null default now(),
    unique (org_id, created_by, client_request_id)
);
"""


@pytest.fixture()
def db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — live Postgres required")
    import psycopg

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(_SCHEMA)
    return TEST_DATABASE_URL


@pytest.fixture()
def seeded(db):
    """One org + one project + one source_asset; returns their ids."""
    import psycopg

    org, project, source = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.organization(id, slug, name) values (%s,%s,'Org')",
            (org, "org-" + org.hex),
        )
        conn.execute(
            "insert into deepresearch.project(id, org_id, created_by, name) "
            "values (%s,%s,%s,'My Project')",
            (project, org, uuid.uuid4()),
        )
        conn.execute(
            "insert into deepresearch.source_asset(id, org_id, created_by, bucket_key, "
            "original_filename, size_bytes) values (%s,%s,%s,'k','clip.mp4',1)",
            (source, org, uuid.uuid4()),
        )
    return db, org, project, source


def _insert_asset(db, org, project, *, asset_type, source_asset_id=None,
                  bucket_key=None, url=None):
    import psycopg

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.project_asset "
            "(id, project_id, org_id, asset_type, source_asset_id, bucket_key, url, title) "
            "values (%s,%s,%s,%s,%s,%s,%s,'t')",
            (uuid.uuid4(), project, org, asset_type, source_asset_id, bucket_key, url),
        )


def test_video_from_source_asset_and_link_from_url_insert(seeded):
    db, org, project, source = seeded
    _insert_asset(db, org, project, asset_type="video", source_asset_id=source)
    _insert_asset(db, org, project, asset_type="link", url="https://example.com")
    _insert_asset(db, org, project, asset_type="image", bucket_key=f"{org}/img.png")


def test_two_refs_violates_exactly_one_of(seeded):
    import psycopg

    db, org, project, source = seeded
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_asset(db, org, project, asset_type="video",
                      source_asset_id=source, url="https://example.com")


def test_link_without_url_is_rejected(seeded):
    import psycopg

    db, org, project, _ = seeded
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_asset(db, org, project, asset_type="link", bucket_key="k")


def test_zero_refs_is_rejected(seeded):
    import psycopg

    db, org, project, _ = seeded
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_asset(db, org, project, asset_type="document")


def test_unknown_asset_type_is_rejected(seeded):
    import psycopg

    db, org, project, _ = seeded
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_asset(db, org, project, asset_type="audio", bucket_key="k")


def test_reel_job_project_link_nulls_on_project_delete(seeded):
    import psycopg

    db, org, project, source = seeded
    job = uuid.uuid4()
    _insert_asset(db, org, project, asset_type="video", source_asset_id=source)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.reel_job "
            "(id, org_id, created_by, client_request_id, project_id) "
            "values (%s,%s,%s,'req-1',%s)",
            (job, org, uuid.uuid4(), project),
        )
        conn.execute("delete from deepresearch.project where id = %s", (project,))
        linked = conn.execute(
            "select project_id from deepresearch.reel_job where id = %s", (job,)
        ).fetchone()[0]
        remaining = conn.execute(
            "select count(*) from deepresearch.project_asset where project_id = %s",
            (project,),
        ).fetchone()[0]
    assert linked is None          # reel survives, link nulled
    assert remaining == 0          # project_assets cascade-deleted
