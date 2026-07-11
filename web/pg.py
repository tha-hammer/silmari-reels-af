"""psycopg adapters against the SHARED user-data Postgres (``deepresearch`` schema).

reels-af *consumes* the root-owned user-data schema; it never owns or vendors the
migrations (``migrations/deepresearch/`` at the monorepo root, applied against the
Railway ``user_data`` DB). This module reads ``DEEPRESEARCH_DATABASE_URL`` and
fails closed when the schema is unset/unreachable/unapplied — the B2 unblock that
lets the service run (and deny) before the root migrations exist.

psycopg is imported lazily inside the connect helper so the fail-closed path
(no URL) works without the driver installed; the live SQL paths are exercised by
``@pytest.mark.integration`` tests against a real Postgres.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from deps import RepositoryUnavailable, SchemaUnavailable
from reel_jobs import ReelJobRef, ReelJobStatus

_VALID_ROLES = ("owner", "admin", "member", "viewer")


def _default_org_id() -> str:
    return os.getenv("REEL_DEFAULT_ORG_ID", "e4e47131-cd9f-4882-9925-194e9db062ca")


def _owner_emails() -> set[str]:
    raw = os.getenv("REEL_OWNER_EMAILS", "maceo.jourdan@gmail.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

# Required schema surface (plan B0.3): table -> required columns.
REQUIRED_SCHEMA: dict[str, set[str]] = {
    "user": {"id", "supertokens_user_id", "status"},
    "organization": {"id", "status"},
    "membership": {"org_id", "user_id", "role", "status"},
    "role_definition": {"role", "permissions"},
    "research_run": {"id", "org_id", "created_by", "status"},
    "reel_job": {
        "id", "org_id", "created_by", "client_request_id", "title", "source_url", "topic",
        "source_research_run_id", "params", "status", "result_ref",
        "execution_id", "created_at", "completed_at",
    },
}


def _database_url() -> str:
    url = os.getenv("DEEPRESEARCH_DATABASE_URL", "")
    if not url:
        raise SchemaUnavailable("DEEPRESEARCH_DATABASE_URL is not set")
    return url


def _connect(url: str):
    try:
        import psycopg  # lazy: fail-closed path needs no driver
    except ImportError as exc:  # pragma: no cover - driver present in the service image
        raise RepositoryUnavailable("psycopg not installed") from exc
    try:
        return psycopg.connect(url, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 - any connect failure fails closed
        raise SchemaUnavailable(f"user-data DB unreachable: {exc}") from exc


def _assert_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'deepresearch'"
        )
        present: dict[str, set[str]] = {}
        for table, column in cur.fetchall():
            present.setdefault(table, set()).add(column)
    for table, columns in REQUIRED_SCHEMA.items():
        have = present.get(table)
        if have is None:
            raise SchemaUnavailable(f"missing table deepresearch.{table}")
        missing = columns - have
        if missing:
            raise SchemaUnavailable(f"deepresearch.{table} missing columns: {sorted(missing)}")


class _SharedSchema:
    """Readiness gate shared by the reader and the repo (checked once per call)."""

    def ensure_ready(self) -> None:
        url = _database_url()
        conn = _connect(url)
        try:
            _assert_schema(conn)
        finally:
            conn.close()


class PgMembershipReader(_SharedSchema):
    """Resolves SuperTokens id → ``(user_id, org_id, role)`` against the shared DB.

    Live SQL is integration-tested; the fail-closed readiness path is unit-tested.
    """

    def resolve_active(self, supertokens_user_id, email, claimed_org_id):
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id from deepresearch.user "
                    "where supertokens_user_id = %s and status = 'active'",
                    (supertokens_user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    # First login: JIT-bootstrap the user into the default org.
                    user_id = self._bootstrap_user(cur, supertokens_user_id, email)
                    conn.commit()
                else:
                    user_id = row[0]
                org_id = self._resolve_org(cur, user_id, claimed_org_id)
                if org_id is None:
                    return None
                cur.execute(
                    "select role from deepresearch.membership "
                    "where org_id = %s and user_id = %s and status = 'active'",
                    (org_id, user_id),
                )
                mrow = cur.fetchone()
                if mrow is None or mrow[0] not in _VALID_ROLES:
                    return None
                return (user_id, org_id, mrow[0])
        finally:
            conn.close()

    @staticmethod
    def _bootstrap_user(cur, supertokens_user_id, email):
        """Idempotently create the app user + default-org membership (first login).

        Owner emails (config) get the ``owner`` role; everyone else ``member``.
        Concurrent first-logins converge via ``on conflict do nothing``.
        """
        org_id = _default_org_id()
        cur.execute(
            "insert into deepresearch.user (id, supertokens_user_id, email, status) "
            "values (%s,%s,%s,'active') on conflict (supertokens_user_id) do nothing "
            "returning id",
            (uuid.uuid4(), supertokens_user_id, email),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            user_id = inserted[0]
        else:
            cur.execute(
                "select id from deepresearch.user where supertokens_user_id = %s",
                (supertokens_user_id,),
            )
            user_id = cur.fetchone()[0]
        role = "owner" if (email or "").lower() in _owner_emails() else "member"
        cur.execute(
            "insert into deepresearch.membership (org_id, user_id, role, status) "
            "values (%s,%s,%s,'active') on conflict (org_id, user_id) do nothing",
            (org_id, user_id, role),
        )
        return user_id

    @staticmethod
    def _resolve_org(cur, user_id, claimed_org_id):  # pragma: no cover - integration
        if claimed_org_id:
            cur.execute(
                "select org_id from deepresearch.membership "
                "where org_id = %s and user_id = %s and status = 'active'",
                (claimed_org_id, user_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
        # single-active-membership fallback (initial single-org only; plan §1)
        cur.execute(
            "select org_id from deepresearch.membership "
            "where user_id = %s and status = 'active' limit 2",
            (user_id,),
        )
        rows = cur.fetchall()
        return rows[0][0] if len(rows) == 1 else None


class PgReelJobRepo(_SharedSchema):
    """Owns ``deepresearch.reel_job`` writes/reads, always scoped by ``org_id``.

    Live SQL is integration-tested; readiness fail-closed is unit-tested.
    """

    def insert_or_get_queued(
        self, ctx, submission, job_id, now, client_request_id
    ):  # pragma: no cover - integration
        import json

        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                # Durable idempotency on (org_id, created_by, client_request_id):
                # a returned id means we created the row; no id means a concurrent
                # or prior request owns the key — fetch its current state.
                cur.execute(
                    "insert into deepresearch.reel_job "
                    "(id, org_id, created_by, client_request_id, title, source_url, topic, "
                    " source_research_run_id, params, status, created_at) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s) "
                    "on conflict (org_id, created_by, client_request_id) do nothing "
                    "returning id",
                    (
                        job_id, ctx.org_id, ctx.user_id, client_request_id, submission.title,
                        submission.source_url, submission.topic,
                        submission.source_research_run_id,
                        json.dumps(submission.params), now,
                    ),
                )
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return ReelJobRef(job_id=job_id, org_id=ctx.org_id,
                                      created_by=ctx.user_id, status="queued", created=True)
                cur.execute(
                    "select id, status, execution_id from deepresearch.reel_job "
                    "where org_id = %s and created_by = %s and client_request_id = %s",
                    (ctx.org_id, ctx.user_id, client_request_id),
                )
                row = cur.fetchone()
                conn.commit()
        finally:
            conn.close()
        return ReelJobRef(job_id=row[0], org_id=ctx.org_id, created_by=ctx.user_id,
                          status=row[1], execution_id=row[2], created=False)

    def attach_execution_id(self, ctx, job_id, execution_id):  # pragma: no cover - integration
        self._update(
            "update deepresearch.reel_job set execution_id = %s "
            "where id = %s and org_id = %s",
            (execution_id, job_id, ctx.org_id),
        )
        return ReelJobRef(job_id=job_id, org_id=ctx.org_id, created_by=ctx.user_id,
                          status="queued", execution_id=execution_id)

    def mark_failed(self, ctx, job_id, reason, completed_at):  # pragma: no cover - integration
        self._update(
            "update deepresearch.reel_job set status = 'failed', completed_at = %s "
            "where id = %s and org_id = %s",
            (completed_at, job_id, ctx.org_id),
        )
        return ReelJobRef(job_id=job_id, org_id=ctx.org_id, created_by=ctx.user_id, status="failed")

    def get_by_execution(self, ctx, execution_id):  # pragma: no cover - integration
        from deps import NotFound

        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, org_id, created_by, status, execution_id, result_ref, completed_at "
                    "from deepresearch.reel_job where execution_id = %s and org_id = %s",
                    (execution_id, ctx.org_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            raise NotFound("reel job not found")
        return ReelJobRef(
            job_id=row[0], org_id=row[1], created_by=row[2], status=row[3],
            execution_id=row[4], result_ref=row[5], completed_at=row[6],
        )

    def update_from_execution(
        self, ctx, execution_id, status: ReelJobStatus, result_ref, completed_at
    ):  # pragma: no cover - integration
        # Terminal monotonicity + result_ref preservation is enforced in SQL:
        # never downgrade a terminal row or overwrite a successful result_ref.
        self._update(
            "update deepresearch.reel_job set status = %s, "
            "result_ref = coalesce(result_ref, %s), completed_at = coalesce(completed_at, %s) "
            "where execution_id = %s and org_id = %s "
            "and status not in ('succeeded','failed','cancelled')",
            (status, result_ref, completed_at, execution_id, ctx.org_id),
        )
        return self.get_by_execution(ctx, execution_id)

    def mark_stale_queued(self, now: datetime) -> int:  # pragma: no cover - integration
        stale_s = int(os.getenv("REEL_DISPATCH_STALE_S", "900"))
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "update deepresearch.reel_job set status = 'failed', completed_at = %s "
                    "where status = 'queued' and execution_id is null "
                    "and created_at < %s - make_interval(secs => %s)",
                    (now, now, stale_s),
                )
                affected = cur.rowcount
                conn.commit()
        finally:
            conn.close()
        return affected

    def _update(self, sql: str, params: tuple):  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
        finally:
            conn.close()


class PgCarouselRepo(_SharedSchema):
    """Carousel read-model repository.

    The concrete SQL behavior lands with the route slices; construction stays
    import-safe and performs no I/O so ``default_deps`` can wire the real slide
    resolver without contacting Postgres.
    """

    def insert_or_get_draft(
        self, ctx, create, carousel_id, now, client_request_id
    ):  # pragma: no cover - implemented with route behavior
        raise NotImplementedError

    def attach_execution_id(self, ctx, carousel_id, execution_id):  # pragma: no cover
        raise NotImplementedError

    def get(self, ctx, carousel_id):  # pragma: no cover
        raise NotImplementedError

    def slide_ref(self, ctx, carousel_id, slide_idx) -> str:  # pragma: no cover
        raise NotImplementedError

    def replace_slide(self, ctx, carousel_id, slide_idx, ref, prompt, status):  # pragma: no cover
        raise NotImplementedError

    def set_status(self, ctx, carousel_id, status):  # pragma: no cover
        raise NotImplementedError

    def draft_slide_refs(self, ctx, carousel_id) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def register_hq_recreate(self, ctx, carousel_id) -> int:  # pragma: no cover
        raise NotImplementedError


def build_identity(reader: PgMembershipReader | None = None):
    """Wire the production identity resolver: real SuperTokens session provider
    (reads the verified session off Flask ``g``) + the DB membership reader with
    JIT bootstrap. Fail-closed: no session → 401; schema unavailable → 503."""
    from auth import ResolverIdentity, SuperTokensSessions

    return ResolverIdentity(SuperTokensSessions(), reader or PgMembershipReader())
