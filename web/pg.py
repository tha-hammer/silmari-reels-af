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
from types import SimpleNamespace

from deps import LocalRun, NotFound, RepositoryUnavailable, SchemaUnavailable
from reel_jobs import ReelJobRef, ReelJobStatus, ResearchRunRef

_VALID_ROLES = ("owner", "admin", "member", "viewer")


def _default_org_id() -> str:
    return os.getenv("REEL_DEFAULT_ORG_ID", "e4e47131-cd9f-4882-9925-194e9db062ca")


def _owner_emails() -> set[str]:
    raw = os.getenv("REEL_OWNER_EMAILS", "maceo.jourdan@gmail.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

# Required schema surface (plan B0.3): table -> required columns.
# Auth-critical schema — the ONLY tables required to RESOLVE IDENTITY. The
# login/identity readiness gate (ResolverIdentity.resolve → ensure_ready) checks
# ONLY these, so a pending FEATURE migration can never brick login (no /login loop).
AUTH_SCHEMA: dict[str, set[str]] = {
    "user": {"id", "supertokens_user_id", "status"},
    "organization": {"id", "status"},
    "membership": {"org_id", "user_id", "role", "status"},
    "role_definition": {"role", "permissions"},
}

# Feature schema — required by the reel/research/carousel FEATURE routes, NOT by
# login. Checked at the feature boundary (ensure_feature_ready), so a pending
# feature migration yields a clean 503 on that feature — never a login loop.
FEATURE_SCHEMA: dict[str, set[str]] = {
    # CI-1: insert_research_run writes 6 columns; fail closed (503) if the root
    # migration lacks execution_id/created_at rather than 500 on the first INSERT.
    "research_run": {"id", "org_id", "created_by", "execution_id", "status", "created_at"},
    "reel_job": {
        "id", "org_id", "created_by", "client_request_id", "title", "source_url", "topic",
        "source_research_run_id", "params", "status", "result_ref",
        "execution_id", "created_at", "completed_at",
    },
    "carousel": {
        "id", "org_id", "created_by", "client_request_id", "status",
        "source_research_run_id", "hq_recreate_count", "execution_id", "created_at",
    },
    "carousel_slide": {"carousel_id", "org_id", "idx", "image_ref", "prompt", "status"},
    # INT-02 consumer-owned tables (root-applied migration; asserted here, fail-closed 503
    # until applied — consumes, never vendors). ``processed_messages`` PK = CloudEvents id
    # (the idempotency key); ``event_cursor`` PK = consumer (reel-af's durable cursor).
    "processed_messages": {"id", "execution_id"},
    "event_cursor": {"consumer", "last_event_sequence"},
}

# Full schema (migrations / docs / feature-readiness reference the union).
REQUIRED_SCHEMA: dict[str, set[str]] = {**AUTH_SCHEMA, **FEATURE_SCHEMA}


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


def _assert_schema(conn, required: dict[str, set[str]]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'deepresearch'"
        )
        present: dict[str, set[str]] = {}
        for table, column in cur.fetchall():
            present.setdefault(table, set()).add(column)
    for table, columns in required.items():
        have = present.get(table)
        if have is None:
            raise SchemaUnavailable(f"missing table deepresearch.{table}")
        missing = columns - have
        if missing:
            raise SchemaUnavailable(f"deepresearch.{table} missing columns: {sorted(missing)}")


class _SharedSchema:
    """Readiness gate shared by the reader and the repo (checked once per call)."""

    def ensure_ready(self) -> None:
        """Auth-path readiness: check ONLY AUTH_SCHEMA. Never gates login on the
        feature tables, so a pending carousel/research migration cannot loop /login."""
        url = _database_url()
        conn = _connect(url)
        try:
            _assert_schema(conn, AUTH_SCHEMA)
        finally:
            conn.close()

    def ensure_feature_ready(self) -> None:
        """Feature-path readiness: check FEATURE_SCHEMA. A pending feature migration
        yields a clean 503 on that feature route — not a login loop. Call at the
        reel/research/carousel feature boundary."""
        url = _database_url()
        conn = _connect(url)
        try:
            _assert_schema(conn, FEATURE_SCHEMA)
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
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                # ISC-25 (GAP1): SELECT source_research_run_id — the writer binds it
                # (insert_or_get_queued) but this reader used to omit it, so provenance
                # was silently null on read. Surfacing it here closes the seam.
                cur.execute(
                    "select id, org_id, created_by, status, execution_id, result_ref, "
                    "completed_at, source_research_run_id "
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
            source_research_run_id=row[7],
        )

    def get(self, ctx, job_id):  # pragma: no cover - integration
        # INT-04 forward lineage: org-scoped read-by-id of a reel job (carries provenance).
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, org_id, created_by, status, execution_id, result_ref, "
                    "completed_at, source_research_run_id "
                    "from deepresearch.reel_job where id = %s and org_id = %s",
                    (job_id, ctx.org_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            raise NotFound("reel job not found")           # conceal cross-org / absent
        return ReelJobRef(
            job_id=row[0], org_id=row[1], created_by=row[2], status=row[3],
            execution_id=row[4], result_ref=row[5], completed_at=row[6],
            source_research_run_id=row[7],
        )

    def reel_jobs_by_source_run(self, ctx, run_id):  # pragma: no cover - integration
        # INT-04 reverse lineage: reel-af-OWNED table ONLY, org-scoped by ctx (no new table).
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, org_id, created_by, status, execution_id, result_ref, "
                    "completed_at, source_research_run_id "
                    "from deepresearch.reel_job "
                    "where source_research_run_id = %s and org_id = %s",
                    (run_id, ctx.org_id),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            ReelJobRef(
                job_id=r[0], org_id=r[1], created_by=r[2], status=r[3],
                execution_id=r[4], result_ref=r[5], completed_at=r[6],
                source_research_run_id=r[7],
            )
            for r in rows
        ]

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

    # ─────────────── research_run persistence (Plan 4, ISC-24) ───────────────

    def insert_research_run(self, ctx, run_id, execution_id, status, now):  # pragma: no cover - integration
        # ROW-FIRST (CI-3): execution_id may be None at insert (dispatch not yet
        # attempted); the route attaches it via update_research_status after dispatch.
        self._update(
            "insert into deepresearch.research_run "
            "(id, org_id, created_by, execution_id, status, created_at) "
            "values (%s,%s,%s,%s,%s,%s)",
            (run_id, ctx.org_id, ctx.user_id, execution_id, status, now),
        )

    def update_research_status(self, ctx, run_id, status=None, execution_id=None):  # pragma: no cover - integration
        # Terminal monotonicity: never downgrade a terminal run (mirrors
        # update_from_execution). Attach execution_id only when supplied. Org-scoped.
        self._update(
            "update deepresearch.research_run set "
            "status = coalesce(%s, status), "
            "execution_id = coalesce(%s, execution_id) "
            "where id = %s and org_id = %s "
            "and status not in ('succeeded','failed','cancelled')",
            (status, execution_id, run_id, ctx.org_id),
        )

    def get_research_run(self, ctx, run_id):  # pragma: no cover - integration
        return self._research_run_one("where id = %s and org_id = %s", (run_id, ctx.org_id))

    def get_research_by_execution(self, ctx, execution_id):  # pragma: no cover - integration
        return self._research_run_one(
            "where execution_id = %s and org_id = %s", (execution_id, ctx.org_id)
        )

    def _research_run_one(self, where: str, params: tuple):  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, org_id, created_by, status, execution_id "
                    "from deepresearch.research_run " + where,
                    params,
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            raise NotFound("research run not found")  # conceal cross-org / absent
        return ResearchRunRef(*row)

    def _update(self, sql: str, params: tuple):  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
        finally:
            conn.close()


class PgCarouselRepo(_SharedSchema):
    """Carousel read-model repository. All reads/writes are org-scoped."""

    def insert_or_get_draft(
        self, ctx, create, carousel_id, now, client_request_id
    ):  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into deepresearch.carousel "
                    "(id, org_id, created_by, client_request_id, status, "
                    " source_research_run_id, created_at) "
                    "values (%s,%s,%s,%s,'draft',%s,%s) "
                    "on conflict (org_id, created_by, client_request_id) do nothing "
                    "returning id",
                    (
                        carousel_id, ctx.org_id, ctx.user_id, client_request_id,
                        create.source_research_run_id, now,
                    ),
                )
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return ReelJobRef(
                        job_id=carousel_id,
                        org_id=ctx.org_id,
                        created_by=ctx.user_id,
                        status="draft",
                        created=True,
                        source_research_run_id=create.source_research_run_id,
                    )
                cur.execute(
                    "select id, org_id, created_by, status, execution_id, "
                    "source_research_run_id "
                    "from deepresearch.carousel "
                    "where org_id = %s and created_by = %s and client_request_id = %s",
                    (ctx.org_id, ctx.user_id, client_request_id),
                )
                row = cur.fetchone()
                conn.commit()
        finally:
            conn.close()
        if row is None:
            raise NotFound("carousel not found")
        return ReelJobRef(
            job_id=row[0],
            org_id=row[1],
            created_by=row[2],
            status=row[3],
            execution_id=row[4],
            source_research_run_id=row[5],
            created=False,
        )

    def attach_execution_id(self, ctx, carousel_id, execution_id):  # pragma: no cover
        row = self._one(
            "update deepresearch.carousel set execution_id = %s "
            "where id = %s and org_id = %s "
            "returning id, org_id, created_by, status, execution_id, source_research_run_id",
            (execution_id, carousel_id, ctx.org_id),
        )
        return ReelJobRef(
            job_id=row[0],
            org_id=row[1],
            created_by=row[2],
            status=row[3],
            execution_id=row[4],
            source_research_run_id=row[5],
            created=False,
        )

    def get(self, ctx, carousel_id):  # pragma: no cover
        # source_research_run_id is surfaced so INT-04 forward lineage can resolve provenance.
        status, source_research_run_id = self._one(
            "select status, source_research_run_id from deepresearch.carousel "
            "where id = %s and org_id = %s",
            (carousel_id, ctx.org_id),
        )
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select idx, image_ref, prompt, status "
                    "from deepresearch.carousel_slide "
                    "where carousel_id = %s and org_id = %s "
                    "order by idx",
                    (carousel_id, ctx.org_id),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return SimpleNamespace(
            status=status,
            source_research_run_id=source_research_run_id,
            slides=[
                {"idx": idx, "image_ref": ref, "prompt": prompt, "status": slide_status}
                for idx, ref, prompt, slide_status in rows
            ],
        )

    def carousels_by_source_run(self, ctx, run_id):  # pragma: no cover - integration
        # INT-04 reverse lineage: reel-af-OWNED carousel table ONLY, org-scoped (no new table).
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, org_id, created_by, status, execution_id, source_research_run_id "
                    "from deepresearch.carousel "
                    "where source_research_run_id = %s and org_id = %s",
                    (run_id, ctx.org_id),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            ReelJobRef(
                job_id=r[0], org_id=r[1], created_by=r[2], status=r[3],
                execution_id=r[4], source_research_run_id=r[5],
            )
            for r in rows
        ]

    def slide_ref(self, ctx, carousel_id, slide_idx) -> str:  # pragma: no cover
        return self._one(
            "select image_ref from deepresearch.carousel_slide "
            "where carousel_id = %s and org_id = %s and idx = %s and image_ref is not null",
            (carousel_id, ctx.org_id, slide_idx),
        )[0]

    def replace_slide(self, ctx, carousel_id, slide_idx, ref, prompt, status):  # pragma: no cover
        self._one(
            "update deepresearch.carousel_slide "
            "set image_ref = %s, prompt = %s, status = %s "
            "where carousel_id = %s and org_id = %s and idx = %s "
            "returning idx",
            (ref, prompt, status, carousel_id, ctx.org_id, slide_idx),
        )

    def set_status(self, ctx, carousel_id, status):  # pragma: no cover
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "update deepresearch.carousel set status = %s "
                    "where id = %s and org_id = %s "
                    "and status not in ('succeeded','failed','cancelled') "
                    "returning status",
                    (status, carousel_id, ctx.org_id),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "select status from deepresearch.carousel "
                        "where id = %s and org_id = %s",
                        (carousel_id, ctx.org_id),
                    )
                    row = cur.fetchone()
                conn.commit()
        finally:
            conn.close()
        if row is None:
            raise NotFound("carousel not found")

    def draft_slide_refs(self, ctx, carousel_id) -> list[str]:  # pragma: no cover
        status = self._one(
            "select status from deepresearch.carousel where id = %s and org_id = %s",
            (carousel_id, ctx.org_id),
        )[0]
        if status in ("succeeded", "failed", "cancelled"):
            return []
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select image_ref from deepresearch.carousel_slide "
                    "where carousel_id = %s and org_id = %s and image_ref is not null "
                    "order by idx",
                    (carousel_id, ctx.org_id),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [row[0] for row in rows]

    def register_hq_recreate(self, ctx, carousel_id) -> int:  # pragma: no cover
        from carousels import HqRecreateCapError

        from reel_af.recreate import HQ_RECREATE_CAP

        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "update deepresearch.carousel "
                    "set hq_recreate_count = hq_recreate_count + 1 "
                    "where id = %s and org_id = %s and hq_recreate_count < %s "
                    "returning hq_recreate_count",
                    (carousel_id, ctx.org_id, HQ_RECREATE_CAP),
                )
                row = cur.fetchone()
                if row is not None:
                    conn.commit()
                    return row[0]
                cur.execute(
                    "select hq_recreate_count from deepresearch.carousel "
                    "where id = %s and org_id = %s",
                    (carousel_id, ctx.org_id),
                )
                existing = cur.fetchone()
                conn.commit()
        finally:
            conn.close()
        if existing is None:
            raise NotFound("carousel not found")
        raise HqRecreateCapError(f"HQ recreate cap reached for {carousel_id}")

    def hq_recreate_count(self, ctx, carousel_id) -> int:  # pragma: no cover
        return self._one(
            "select hq_recreate_count from deepresearch.carousel "
            "where id = %s and org_id = %s",
            (carousel_id, ctx.org_id),
        )[0]

    def _one(self, sql: str, params: tuple):  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                conn.commit()
        finally:
            conn.close()
        if row is None:
            raise NotFound("carousel not found")
        return row


class PgEventConsumerStore(_SharedSchema):
    """reel-af's OWN durable dedup + cursor + resolution + provenance-stamp store (INT-02).

    Implements ``ProcessedMessagesPort`` + ``EventCursorPort`` + ``LocalRunResolverPort``.
    Writes ONLY reel-af's own tables (``processed_messages``, ``event_cursor``, and the
    ``reel_job``/``carousel`` provenance column) — NEVER ``deepresearch.research_run`` or any
    owner table (A3, C-Own). Background-safe: scoped by the resolved local row's ``org_id``,
    never a request ``ctx``.
    """

    # ─────────────── EventCursorPort (standalone advance for deduped/malformed) ───────────────

    def get(self, consumer: str) -> int:  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select last_event_sequence from deepresearch.event_cursor "
                    "where consumer = %s",
                    (consumer,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row[0] if row else 0

    def advance(self, consumer: str, seq: int) -> None:  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                self._advance_cursor(cur, consumer, seq)
                conn.commit()
        finally:
            conn.close()

    # ─────────────── ProcessedMessagesPort ───────────────

    def already_processed(self, cloudevents_id: str) -> bool:  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from deepresearch.processed_messages where id = %s",
                    (cloudevents_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row is not None

    def mark(self, cloudevents_id: str, execution_id: str | None) -> None:  # pragma: no cover
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into deepresearch.processed_messages (id, execution_id) "
                    "values (%s, %s) on conflict (id) do nothing",
                    (cloudevents_id, execution_id),
                )
                conn.commit()
        finally:
            conn.close()

    # ─────────────── LocalRunResolverPort ───────────────

    def resolve(self, execution_id: str) -> LocalRun | None:  # pragma: no cover - integration
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                run = self._resolve_local_run(cur, execution_id)
        finally:
            conn.close()
        return run

    # ─────────────── the C5 one-transaction effect ───────────────

    def stamp_dedup_advance(self, event: dict, consumer: str):  # pragma: no cover - integration
        """ONE transaction (C5): insert ``processed_messages(id)`` ON CONFLICT DO NOTHING;
        if fresh, resolve ``execution_id`` → the local ``research_run`` and idempotently stamp
        ``source_research_run_id`` = the resolved LOCAL UUID (never the text id) scoped to the
        resolved ``org_id`` (C-Own); then advance the cursor. All-or-nothing — no crash window
        splits the stamp from the dedup mark or the cursor advance."""
        from events import ConsumeResult

        seq = event["sequence"]
        cloudevents_id = event["id"]
        execution_id = event["subject"]
        conn = _connect(_database_url())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into deepresearch.processed_messages (id, execution_id) "
                    "values (%s, %s) on conflict (id) do nothing returning id",
                    (cloudevents_id, execution_id),
                )
                first_seen = cur.fetchone() is not None
                local_run_found = False
                if first_seen:
                    run = self._resolve_local_run(cur, execution_id)
                    if run is not None:
                        local_run_found = True
                        self._stamp_provenance(cur, run, execution_id)
                self._advance_cursor(cur, consumer, seq)
                conn.commit()
        finally:
            conn.close()
        return ConsumeResult(first_seen=first_seen, local_run_found=local_run_found)

    # ─────────────── SQL helpers (share one cursor/tx) ───────────────

    @staticmethod
    def _resolve_local_run(cur, execution_id: str) -> LocalRun | None:  # pragma: no cover
        # READ of reel-af's OWN local research_run (never an owner write — A3).
        cur.execute(
            "select id, org_id, created_by from deepresearch.research_run "
            "where execution_id = %s",
            (execution_id,),
        )
        row = cur.fetchone()
        return LocalRun(id=row[0], org_id=row[1], created_by=row[2]) if row else None

    @staticmethod
    def _stamp_provenance(cur, run: LocalRun, execution_id: str) -> None:  # pragma: no cover
        # Idempotent (null-guarded), org-scoped stamp of the resolved LOCAL UUID. Correlated
        # by the research execution_id (the dispatched-run §1a case). The broader
        # create-from-research correlation column is Plan 6's Open-Seam; this stamp only ever
        # touches reel-af's OWN reel_job/carousel rows in the resolved org (C-Own / C6).
        for table in ("reel_job", "carousel"):
            cur.execute(
                f"update deepresearch.{table} set source_research_run_id = %s "
                "where source_research_run_id is null and org_id = %s and execution_id = %s",
                (run.id, run.org_id, execution_id),
            )

    @staticmethod
    def _advance_cursor(cur, consumer: str, seq: int) -> None:  # pragma: no cover
        # Monotonic upsert: never regress the cursor (greatest wins).
        cur.execute(
            "insert into deepresearch.event_cursor (consumer, last_event_sequence) "
            "values (%s, %s) on conflict (consumer) do update set "
            "last_event_sequence = greatest("
            "deepresearch.event_cursor.last_event_sequence, excluded.last_event_sequence)",
            (consumer, seq),
        )


def build_identity(reader: PgMembershipReader | None = None):
    """Wire the production identity resolver: an ordered ``CompositeSessions`` of
    the machine-token provider (checked first) then the real SuperTokens session
    provider (reads the verified session off Flask ``g``), over the DB membership
    reader with JIT bootstrap. Fail-closed: no session → 401; schema unavailable
    → 503. The service seam is disabled (no-op) when ``REEL_AF_SERVICE_TOKEN`` is
    unset, so resolution is byte-identical to the pure-SuperTokens path."""
    from auth import (
        REEL_AF_SERVICE_TOKEN_ENV,
        SERVICE_EMAIL,
        SERVICE_USER_ID,
        CompositeSessions,
        ResolverIdentity,
        ServiceTokenSessions,
        SuperTokensSessions,
    )

    service_token = os.getenv(REEL_AF_SERVICE_TOKEN_ENV)
    sessions = CompositeSessions(
        [
            ServiceTokenSessions(service_token, SERVICE_USER_ID, SERVICE_EMAIL),
            SuperTokensSessions(),
        ]
    )
    return ResolverIdentity(sessions, reader or PgMembershipReader())
