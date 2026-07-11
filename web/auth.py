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
