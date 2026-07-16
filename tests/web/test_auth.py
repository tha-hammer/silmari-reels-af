"""Machine-token (M2M) auth seam — unit behaviors 1–4.

Covers the new ``SessionProvider`` layer that reel-af previously left untested:
``_ServiceSession`` (B1), ``ServiceTokenSessions`` validate/isolate/fail-closed
(B2), ``CompositeSessions`` ordered fall-through (B3), and ``build_identity``
wiring the composite from env (B4). No I/O — SuperTokens/Postgres never load.
"""

from __future__ import annotations

import auth
import pg
import pytest
from conftest import ORG_ID, USER_ID, FakeMembershipReader
from deps import Unauthorized
from hypothesis import given
from hypothesis import strategies as st


class _Req:
    """Minimal request stub exposing ``.headers`` (what ``get_session`` reads)."""

    def __init__(self, auth_header=None):
        self.headers = {} if auth_header is None else {"Authorization": auth_header}


# ───────────────────────── Behavior 1: _ServiceSession ─────────────────────────


def test_service_session_exposes_fixed_identity():
    s = auth._ServiceSession(user_id="svc:a1-pipeline", email="a1-pipeline+service@silmari.ai")
    assert s.get_user_id() == "svc:a1-pipeline"
    assert s.get_email() == "a1-pipeline+service@silmari.ai"
    assert s.get_active_org_id() is None


def test_service_session_active_org_never_raises_with_no_email():
    s = auth._ServiceSession(user_id="svc:a1-pipeline", email=None)
    assert s.get_email() is None
    assert s.get_active_org_id() is None


def test_service_session_satisfies_sessionlike_protocol():
    s = auth._ServiceSession(user_id="svc:a1-pipeline", email=None)
    assert isinstance(s, auth.SessionLike)  # @runtime_checkable structural check


# ─────────────────────── Behavior 2: ServiceTokenSessions ───────────────────────


def _provider(token, email=None):
    return auth.ServiceTokenSessions(token=token, user_id="svc:a1-pipeline", email=email)


def test_valid_bearer_returns_service_session():
    s = _provider("secret-123", email="a1-pipeline+service@silmari.ai").get_session(
        _Req("Bearer secret-123")
    )
    assert s is not None
    assert s.get_user_id() == "svc:a1-pipeline"
    assert s.get_email() == "a1-pipeline+service@silmari.ai"


def test_wrong_bearer_returns_none():
    assert _provider("secret-123").get_session(_Req("Bearer nope")) is None


def test_missing_header_returns_none():
    assert _provider("secret-123").get_session(_Req(None)) is None


def test_non_bearer_scheme_returns_none():
    assert _provider("secret-123").get_session(_Req("Basic secret-123")) is None


def test_bearer_scheme_is_case_sensitive():
    # Documented: exact "Bearer " prefix only; a lower-case scheme falls through.
    assert _provider("secret-123").get_session(_Req("bearer secret-123")) is None


def test_empty_bearer_value_returns_none():
    assert _provider("secret-123").get_session(_Req("Bearer ")) is None


def test_unset_token_disables_seam_even_with_valid_header():
    # Environment isolation / fail-closed: no configured token → seam off entirely.
    assert _provider(None).get_session(_Req("Bearer anything")) is None
    assert _provider("").get_session(_Req("Bearer ")) is None
    assert _provider("").get_session(_Req("Bearer whatever")) is None


@given(x=st.text())
def test_property_wrong_secret_never_authenticates(x):
    token = "secret-123"
    result = _provider(token).get_session(_Req(f"Bearer {x}"))
    if x == token:
        assert result is not None and result.get_user_id() == "svc:a1-pipeline"
    else:
        assert result is None


# ───────────────────────── Behavior 3: CompositeSessions ─────────────────────────


class _P:
    def __init__(self, out):
        self._out = out

    def get_session(self, _req):
        return self._out


def test_composite_returns_first_non_none():
    sess = object()
    assert auth.CompositeSessions([_P(sess), _P(None)]).get_session(_Req()) is sess


def test_composite_falls_through_to_next():
    sess = object()
    assert auth.CompositeSessions([_P(None), _P(sess)]).get_session(_Req()) is sess


def test_composite_all_none_is_none():
    assert auth.CompositeSessions([_P(None), _P(None)]).get_session(_Req()) is None


def test_composite_empty_list_is_none():
    assert auth.CompositeSessions([]).get_session(_Req()) is None


def test_composite_does_not_swallow_a_raising_provider():
    class _Boom:
        def get_session(self, _req):
            raise RuntimeError("misbehaving provider")

    with pytest.raises(RuntimeError):
        auth.CompositeSessions([_Boom(), _P(object())]).get_session(_Req())


@given(outs=st.lists(st.one_of(st.none(), st.builds(object)), min_size=0, max_size=6))
def test_property_composite_is_first_non_none_in_order(outs):
    expected = next((o for o in outs if o is not None), None)
    got = auth.CompositeSessions([_P(o) for o in outs]).get_session(_Req())
    assert got is expected


# ─────────────────────── Behavior 4: build_identity from env ───────────────────────


def test_build_identity_authenticates_service_token(monkeypatch):
    monkeypatch.setenv(auth.REEL_AF_SERVICE_TOKEN_ENV, "secret-123")
    reader = FakeMembershipReader(seed={auth.SERVICE_USER_ID: (USER_ID, ORG_ID, "member")})
    ident = pg.build_identity(reader=reader)  # sole composition site (pg.py)
    ctx = ident.resolve(_Req("Bearer secret-123"))
    assert ctx.role == "member"
    assert ctx.user_id == USER_ID and ctx.org_id == ORG_ID
    assert ctx.supertokens_user_id == auth.SERVICE_USER_ID


def test_build_identity_disabled_when_token_unset(monkeypatch):
    monkeypatch.delenv(auth.REEL_AF_SERVICE_TOKEN_ENV, raising=False)
    reader = FakeMembershipReader(seed={})
    ident = pg.build_identity(reader=reader)
    with pytest.raises(Unauthorized):  # no session, no bypass
        ident.resolve(_Req("Bearer secret-123"))


def test_build_identity_wrong_token_falls_through_to_401(monkeypatch):
    monkeypatch.setenv(auth.REEL_AF_SERVICE_TOKEN_ENV, "secret-123")
    reader = FakeMembershipReader(seed={auth.SERVICE_USER_ID: (USER_ID, ORG_ID, "member")})
    ident = pg.build_identity(reader=reader)
    with pytest.raises(Unauthorized):
        ident.resolve(_Req("Bearer wrong"))


def test_service_identity_constants_are_named_not_scattered():
    assert auth.SERVICE_USER_ID == "svc:a1-pipeline"
    assert auth.SERVICE_EMAIL == "a1-pipeline+service@silmari.ai"
    assert auth.REEL_AF_SERVICE_TOKEN_ENV == "REEL_AF_SERVICE_TOKEN"
