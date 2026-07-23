"""Integration: PgProjectRepo / PgProjectAssetRepo SQL contract (AF-4pz.4/.5).

Runs ONLY when ``TEST_DATABASE_URL`` points at a live Postgres. Schema fixture
mirrors migration 115 (test fixture, not a vendored migration).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from deps import AuthContext, NotFound
from pg import PgProjectAssetRepo, PgProjectRepo

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)

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
    asset_type text not null check (asset_type in ('video','image','link','document')),
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
def orgs(db):
    import psycopg

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(db, autocommit=True) as conn:
        for org_id in (org_a, org_b):
            conn.execute(
                "insert into deepresearch.organization(id, slug, name) values (%s,%s,'Org')",
                (org_id, "org-" + org_id.hex),
            )
    return org_a, org_b


def _ctx(org_id, user_id=None):
    return AuthContext(user_id=user_id or uuid.uuid4(), org_id=org_id, role="member",
                       supertokens_user_id="st-x")


def test_project_crud_round_trip_org_scoped(orgs):
    org_a, org_b = orgs
    user = uuid.uuid4()
    repo = PgProjectRepo()
    project_id = uuid.uuid4()

    created = repo.create(_ctx(org_a, user), project_id=project_id,
                          name="Reels Q3", description="d", now=NOW)
    assert created.created_by == user

    assert [p.project_id for p in repo.list_for_org(_ctx(org_a))] == [project_id]
    assert repo.list_for_org(_ctx(org_b)) == []          # org-scoped
    with pytest.raises(NotFound):
        repo.get(_ctx(org_b), project_id)                # foreign concealed

    renamed = repo.update(_ctx(org_a), project_id, name="Renamed",
                          now=NOW + timedelta(minutes=1))
    assert renamed.name == "Renamed" and renamed.description == "d"

    repo.soft_delete(_ctx(org_a), project_id, now=NOW + timedelta(minutes=2))
    assert repo.list_for_org(_ctx(org_a)) == []
    with pytest.raises(NotFound):
        repo.get(_ctx(org_a), project_id)                # soft-deleted concealed
    with pytest.raises(NotFound):
        repo.soft_delete(_ctx(org_a), project_id, now=NOW)   # idempotent conceal


def test_project_assets_add_list_soft_delete(orgs):
    import psycopg

    org_a, org_b = orgs
    projects, assets = PgProjectRepo(), PgProjectAssetRepo()
    project_id, source_id = uuid.uuid4(), uuid.uuid4()
    projects.create(_ctx(org_a), project_id=project_id, name="P", description=None, now=NOW)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "insert into deepresearch.source_asset(id, org_id, created_by, bucket_key, "
            "original_filename, size_bytes) values (%s,%s,%s,'k','c.mp4',1)",
            (source_id, org_a, uuid.uuid4()),
        )

    video = assets.add(_ctx(org_a), asset_id=uuid.uuid4(), project_id=project_id,
                       asset_type="video", source_asset_id=source_id, bucket_key=None,
                       url=None, title="clip", now=NOW)
    link = assets.add(_ctx(org_a), asset_id=uuid.uuid4(), project_id=project_id,
                      asset_type="link", source_asset_id=None, bucket_key=None,
                      url="https://example.com", title=None,
                      now=NOW + timedelta(minutes=1))

    listed = assets.list_for_project(_ctx(org_a), project_id)
    assert [a.asset_id for a in listed] == [link.asset_id, video.asset_id]  # newest first
    assert assets.list_for_project(_ctx(org_b), project_id) == []           # org-scoped

    assets.soft_delete(_ctx(org_a), project_id, link.asset_id, now=NOW)
    assert [a.asset_id for a in assets.list_for_project(_ctx(org_a), project_id)] == [
        video.asset_id
    ]
    with pytest.raises(NotFound):
        assets.soft_delete(_ctx(org_a), project_id, link.asset_id, now=NOW)
    with pytest.raises(NotFound):
        assets.soft_delete(_ctx(org_b), project_id, video.asset_id, now=NOW)
