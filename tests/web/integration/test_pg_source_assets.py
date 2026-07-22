"""Integration: PgSourceAssetRepo SQL contract against a live Postgres (AF-02f).

Runs ONLY when ``TEST_DATABASE_URL`` points at a live Postgres; otherwise
skipped. The schema below is a **test fixture**, not a vendored migration — the
production schema is root-owned (`migrations/deepresearch/114_create_source_asset.sql`).
It mirrors the columns/keys the adapter depends on so the SQL contract
(org-scoping, soft-delete exclusion, newest-first ordering) is provable — what
fakes can't show.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from deps import AuthContext
from pg import PgSourceAssetRepo

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
-- Mirrors migration 114: created_by has NO user FK (see 113 / AF-ggp).
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


def _ctx(org_id, user_id):
    return AuthContext(user_id=user_id, org_id=org_id, role="member",
                       supertokens_user_id="st-x")


def _create(repo, ctx, *, at=NOW, name="clip.mp4"):
    asset_id = uuid.uuid4()
    repo.create(
        ctx, asset_id=asset_id, bucket_key=f"{ctx.org_id}/{asset_id.hex}-{name}",
        original_filename=name, content_type="video/mp4", size_bytes=42,
        checksum="sha256:abc", now=at,
    )
    return asset_id


def test_create_then_list_round_trip(orgs):
    org_a, _ = orgs
    user = uuid.uuid4()
    repo = PgSourceAssetRepo()
    asset_id = _create(repo, _ctx(org_a, user))

    assets = repo.list_for_org(_ctx(org_a, user))

    assert [a.asset_id for a in assets] == [asset_id]
    ref = assets[0]
    assert ref.org_id == org_a and ref.created_by == user
    assert ref.original_filename == "clip.mp4"
    assert ref.size_bytes == 42 and ref.checksum == "sha256:abc"
    assert ref.status == "stored"


def test_list_is_org_scoped(orgs):
    org_a, org_b = orgs
    repo = PgSourceAssetRepo()
    _create(repo, _ctx(org_a, uuid.uuid4()))
    foreign = _create(repo, _ctx(org_b, uuid.uuid4()))

    listed = repo.list_for_org(_ctx(org_a, uuid.uuid4()))

    assert foreign not in [a.asset_id for a in listed]
    assert all(a.org_id == org_a for a in listed)


def test_list_excludes_soft_deleted_and_orders_newest_first(orgs):
    import psycopg

    org_a, _ = orgs
    user = uuid.uuid4()
    repo = PgSourceAssetRepo()
    older = _create(repo, _ctx(org_a, user), at=NOW - timedelta(hours=1), name="old.mp4")
    newer = _create(repo, _ctx(org_a, user), at=NOW, name="new.mp4")
    deleted = _create(repo, _ctx(org_a, user), at=NOW + timedelta(hours=1), name="gone.mp4")
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "update deepresearch.source_asset set deleted_at = now() where id = %s",
            (deleted,),
        )

    assets = repo.list_for_org(_ctx(org_a, user))

    assert [a.asset_id for a in assets] == [newer, older]
