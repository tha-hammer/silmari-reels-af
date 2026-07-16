"""Fail-closed identity resolution for reel-af-ui.

Mirrors the *shape* of deep-research's tenancy seam
(``ui/tenancy/{ports,identity,context}.py``): a minimal ``SessionLike`` read
off a verified SuperTokens session, an injected reader that resolves the
SuperTokens id to internal identity against the shared ``deepresearch`` schema,
and a resolver that fails closed before any repo/control-plane call.

Divergences from deep-research (per plan §1 + review W5): reels resolves ``role``
(for CS-5 authorization) and does NOT use ``DefaultOrgIdentity`` — it resolves
real ``deepresearch.membership``/``role_definition`` rows, JIT-bootstrapping the
SuperTokens user into the default org on first login.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Protocol, runtime_checkable

from deps import AuthContext, Forbidden, Role, Unauthorized


@runtime_checkable
class SessionLike(Protocol):
    """The slice of a verified session the tenancy layer reads."""

    def get_user_id(self) -> str: ...

    def get_email(self) -> str | None:
        """Verified email (drives owner-role assignment on JIT bootstrap)."""
        ...

    def get_active_org_id(self) -> str | None:
        """Active org from an access-token claim, or ``None`` (fallback applies)."""
        ...


@runtime_checkable
class SessionProvider(Protocol):
    """Verifies + returns the request session, or ``None`` when unauthenticated."""

    def get_session(self, request) -> SessionLike | None: ...


@runtime_checkable
class MembershipReader(Protocol):
    """Resolves a SuperTokens id to internal ``(user_id, org_id, role)``.

    ``ensure_ready`` fails closed (``SchemaUnavailable``/503) when the shared
    user-data DB is unset/unreachable or required tables are absent (B2). On
    first login for an unknown SuperTokens user, ``resolve_active`` JIT-bootstraps
    the user into the default org, then resolves membership + role.
    """

    def ensure_ready(self) -> None: ...

    def resolve_active(
        self, supertokens_user_id: str, email: str | None, claimed_org_id: str | None
    ) -> tuple[uuid.UUID, uuid.UUID, Role] | None: ...


class ResolverIdentity:
    """`IdentityProvider` implementation: session → readiness → fail-closed context."""

    def __init__(self, sessions: SessionProvider, reader: MembershipReader) -> None:
        self._sessions = sessions
        self._reader = reader

    def resolve(self, request) -> AuthContext:
        session = self._sessions.get_session(request)
        if session is None:
            raise Unauthorized("no session")
        supertokens_user_id = session.get_user_id()
        if not supertokens_user_id:
            raise Unauthorized("session has no user id")

        # Readiness gate BEFORE resolution — raises SchemaUnavailable (503) with
        # no control-plane call when the shared schema is unavailable (B2).
        self._reader.ensure_ready()

        resolved = self._reader.resolve_active(
            supertokens_user_id, session.get_email(), session.get_active_org_id()
        )
        if resolved is None:
            # Missing/inactive user, missing/ambiguous org, revoked membership,
            # unknown role — routes never see the reason (plan §4).
            raise Forbidden("no active membership for this org")
        user_id, org_id, role = resolved
        return AuthContext(
            user_id=user_id,
            org_id=org_id,
            role=role,
            supertokens_user_id=supertokens_user_id,
        )


class UnauthenticatedSessions:
    """Fallback session provider (tests / SuperTokens disabled): always 401."""

    def get_session(self, _request) -> SessionLike | None:
        return None


class _StSession:
    """Adapts a raw SuperTokens session (+ resolved email) to ``SessionLike``."""

    def __init__(self, st_session, email: str | None) -> None:
        self._s = st_session
        self._email = email

    def get_user_id(self) -> str:
        return self._s.get_user_id()

    def get_email(self) -> str | None:
        return self._email

    def get_active_org_id(self) -> str | None:
        # Single-org deployment: the org comes from the DB membership fallback.
        # A future multi-org build reads an access-token claim here.
        try:
            payload = self._s.get_access_token_payload() or {}
        except Exception:  # noqa: BLE001 - absent claim is not an error
            return None
        return payload.get("activeOrgId")


class SuperTokensSessions:
    """Production session provider: reads the verified session SuperTokens placed
    on Flask ``g`` (via the ``verify_session`` decorator/middleware) and resolves
    the user's email from the emailpassword recipe."""

    def get_session(self, _request) -> SessionLike | None:
        from flask import g

        try:
            st_session = getattr(g, "supertokens", None)
        except RuntimeError:
            return None  # no app/request context → no session (fail closed)
        if st_session is None:
            return None
        return _StSession(st_session, _lookup_email(st_session.get_user_id()))


def _lookup_email(supertokens_user_id: str) -> str | None:
    try:  # pragma: no cover - requires live SuperTokens core
        from supertokens_python.syncio import get_user

        user = get_user(supertokens_user_id)
        emails = getattr(user, "emails", None) if user else None
        return emails[0] if emails else None
    except Exception:  # noqa: BLE001 - email is best-effort (owner detection only)
        return None


# ─────────────────────── machine-token (M2M) auth seam ───────────────────────
# A scoped shared secret that resolves a non-human caller (A1) to a dedicated
# least-privilege service *member*. The minimal shared-secret form of OAuth2
# client-credentials, dropped into the existing swappable SessionProvider seam;
# the upgrade path (JWKS client-credentials, RFC 8693, mTLS) reuses this seam.
# Enabled only when REEL_AF_SERVICE_TOKEN is set — unset is fail-closed and
# byte-identical to the pure-SuperTokens path.
REEL_AF_SERVICE_TOKEN_ENV = "REEL_AF_SERVICE_TOKEN"
SERVICE_USER_ID = "svc:a1-pipeline"
SERVICE_EMAIL = "a1-pipeline+service@silmari.ai"

_BEARER_PREFIX = "Bearer "  # scheme match is case-sensitive by design (documented)


class _ServiceSession:
    """A fixed-identity ``SessionLike`` for the machine caller (no SuperTokens).

    Mirrors ``_StSession``'s shape: three read-only accessors over stored fields.
    ``get_active_org_id`` returns ``None`` so the single-org membership fallback
    (``PgMembershipReader._resolve_org``) resolves the org — never raises."""

    def __init__(self, user_id: str, email: str | None) -> None:
        self._user_id = user_id
        self._email = email

    def get_user_id(self) -> str:
        return self._user_id

    def get_email(self) -> str | None:
        return self._email

    def get_active_org_id(self) -> str | None:
        return None


def _extract_bearer(headers) -> str | None:
    """Return the token from an exact ``Bearer <token>`` Authorization header, or
    ``None`` (missing header, wrong scheme, or empty token). A pure question with
    no side effects."""
    raw = headers.get("Authorization") if headers is not None else None
    if not raw or not raw.startswith(_BEARER_PREFIX):
        return None
    token = raw[len(_BEARER_PREFIX):]
    return token or None


class ServiceTokenSessions:
    """`SessionProvider` that authenticates a single scoped service token.

    Fail-closed and isolating:
    - unset/empty configured token → seam disabled (``None`` even for a valid
      header), so the environment isolates the machine path when unconfigured;
    - a missing header or non-``Bearer`` scheme → ``None`` (fall through);
    - a wrong token → ``None`` (constant-time compare; never ``==``), so a wrong
      secret NEVER yields a service session;
    - only the exact configured token → a ``_ServiceSession`` for the service id.
    """

    def __init__(self, token: str | None, user_id: str, email: str | None) -> None:
        self._token = token
        self._user_id = user_id
        self._email = email

    def get_session(self, request) -> SessionLike | None:
        if not self._token:  # unset/empty → seam disabled (fail-closed) — cheapest, first
            return None
        presented = _extract_bearer(getattr(request, "headers", None))
        if presented is None:
            return None
        # Constant-time over UTF-8 bytes (never ==). Encoding first also keeps a
        # non-ASCII presented token a clean fall-through, not a 500.
        if not hmac.compare_digest(presented.encode("utf-8"), self._token.encode("utf-8")):
            return None
        return _ServiceSession(self._user_id, self._email)


class CompositeSessions:
    """`SessionProvider` that tries providers in order and returns the first
    non-``None`` session (all ``None`` → ``None``). Order is load-bearing: the
    service-token provider is checked before SuperTokens. A provider that raises
    is NOT swallowed — a misbehaving provider must fail loud."""

    def __init__(self, providers) -> None:
        self._providers = list(providers)

    def get_session(self, request) -> SessionLike | None:
        return next(
            (s for p in self._providers if (s := p.get_session(request)) is not None),
            None,
        )
