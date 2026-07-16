"""Behavior 6 — the seeded A1 service member resolves via real Postgres (BLOCKING).

Runs only under ``-m integration`` with ``TEST_DATABASE_URL`` pointing at live
Postgres; the ``db`` fixture skips otherwise (never skipped-to-green under
``-m integration`` — a missing URL is a red skip, per the closure rule). The
seed statements here are the SAME as ``ops/seed_a1_service_user.sql`` (the ops
runbook deliverable), mirrored into the schema fixture.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
DEFAULT_ORG_ID = "e4e47131-cd9f-4882-9925-194e9db062ca"
SERVICE_ST_ID = "svc:a1-pipeline"
SERVICE_EMAIL = "a1-pipeline+service@silmari.ai"

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
"""


@pytest.fixture()
def db(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — run with -m integration to enforce closure")
    import psycopg

    monkeypatch.setenv("DEEPRESEARCH_DATABASE_URL", TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(_SCHEMA)
        # Default org must exist before membership FK. The service user + membership
        # are the SAME statements as ops/seed_a1_service_user.sql.
        conn.execute(
            "insert into deepresearch.organization(id, slug, name) "
            "values (%s, 'default', 'Default Org') on conflict do nothing",
            (DEFAULT_ORG_ID,),
        )
        conn.execute(
            'insert into deepresearch."user"(id, supertokens_user_id, email, status) '
            "values (%s, %s, %s, 'active') on conflict (supertokens_user_id) do nothing",
            (uuid.uuid4(), SERVICE_ST_ID, SERVICE_EMAIL),
        )
        conn.execute(
            "insert into deepresearch.membership(org_id, user_id, role, status) "
            'select %s, u.id, \'member\', \'active\' from deepresearch."user" u '
            "where u.supertokens_user_id = %s on conflict (org_id, user_id) do nothing",
            (DEFAULT_ORG_ID, SERVICE_ST_ID),
        )
    return TEST_DATABASE_URL


def test_seeded_service_user_resolves_to_member(db):
    from pg import PgMembershipReader

    reader = PgMembershipReader()
    user_id, org_id, role = reader.resolve_active(SERVICE_ST_ID, SERVICE_EMAIL, None)
    assert role == "member"
    assert str(org_id) == DEFAULT_ORG_ID
    assert isinstance(user_id, uuid.UUID)
