"""B14 - control-plane port: header stripping, API-key injection, response filtering.

The CP client is server-to-server: it builds its OWN request headers (never
forwards the browser's cookie/session/auth headers) and injects ``X-API-Key``
server-side. Transport/JSON failures fail closed as 502.
"""

from __future__ import annotations

import pytest


class FakeResp:
    def __init__(self, status=202, payload=None, headers=None, raise_json=False, content=b"x"):
        self.status_code = status
        self._payload = payload if payload is not None else {"execution_id": "e1"}
        self.headers = headers or {}
        self.content = content
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


def _patch_requests(monkeypatch):
    calls = {}

    def fake_request(method, url, json=None, headers=None, timeout=None):
        calls.update(method=method, url=url, json=json, headers=headers, timeout=timeout)
        return calls.get("_resp", FakeResp())

    import control_plane

    monkeypatch.setattr(control_plane.requests, "request", fake_request)
    return calls


def _cp(monkeypatch, **env):
    monkeypatch.setenv("AGENTFIELD_SERVER", env.get("server", "http://cp.internal:8080"))
    monkeypatch.setenv("AGENTFIELD_API_KEY", env.get("key", "secret-key"))
    monkeypatch.setenv("PROXY_TIMEOUT_S", env.get("timeout", "42"))
    from control_plane import HttpControlPlane

    return HttpControlPlane()


def test_dispatch_injects_api_key_and_forwards_body_and_timeout(monkeypatch):
    calls = _patch_requests(monkeypatch)
    cp = _cp(monkeypatch)
    status, body, _headers = cp.dispatch_async("reel-af.reel_topic_to_reel", {"input": {"topic": "x"}})

    assert status == 202 and body == {"execution_id": "e1"}
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/api/v1/execute/async/reel-af.reel_topic_to_reel")
    assert calls["json"] == {"input": {"topic": "x"}}      # body forwarded
    assert calls["timeout"] == 42.0                          # timeout preserved
    assert calls["headers"]["X-API-Key"] == "secret-key"     # key injected server-side


def test_no_browser_or_session_headers_are_sent(monkeypatch):
    calls = _patch_requests(monkeypatch)
    cp = _cp(monkeypatch)
    cp.dispatch_async("reel-af.reel_topic_to_reel", {"input": {}})

    sent = {k.lower() for k in calls["headers"]}
    # The client builds its own headers — none of the browser/session headers leak.
    for forbidden in ("cookie", "authorization", "origin", "referer", "host",
                      "st-access-token", "anti-csrf", "rid"):
        assert forbidden not in sent
    assert sent == {"content-type", "x-api-key"}


def test_no_api_key_header_when_unset(monkeypatch):
    calls = _patch_requests(monkeypatch)
    monkeypatch.delenv("AGENTFIELD_API_KEY", raising=False)
    monkeypatch.setenv("AGENTFIELD_SERVER", "http://cp.internal:8080")
    from control_plane import HttpControlPlane

    HttpControlPlane().dispatch_async("reel-af.reel_topic_to_reel", {"input": {}})
    assert "X-API-Key" not in calls["headers"]


def test_hop_by_hop_response_headers_filtered(monkeypatch):
    calls = _patch_requests(monkeypatch)
    calls["_resp"] = FakeResp(
        headers={"Content-Type": "application/json", "Content-Length": "12",
                 "Transfer-Encoding": "chunked", "Connection": "keep-alive"}
    )
    cp = _cp(monkeypatch)
    _status, _body, headers = cp.dispatch_async("reel-af.reel_topic_to_reel", {"input": {}})
    lower = {k.lower() for k in headers}
    assert "content-type" in lower
    assert "content-length" not in lower and "transfer-encoding" not in lower
    assert "connection" not in lower


def test_transport_error_is_502(monkeypatch):
    import control_plane
    from deps import BadGateway

    def boom(*_a, **_k):
        raise control_plane.requests.RequestException("dead")

    monkeypatch.setattr(control_plane.requests, "request", boom)
    cp = _cp(monkeypatch)
    with pytest.raises(BadGateway):
        cp.dispatch_async("reel-af.reel_topic_to_reel", {"input": {}})


def test_invalid_json_success_is_502(monkeypatch):
    calls = _patch_requests(monkeypatch)
    calls["_resp"] = FakeResp(raise_json=True, content=b"<html>")
    from deps import BadGateway

    cp = _cp(monkeypatch)
    with pytest.raises(BadGateway):
        cp.dispatch_async("reel-af.reel_topic_to_reel", {"input": {}})
