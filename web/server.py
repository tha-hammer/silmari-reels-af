"""reel-af · Cutting Room — static UI + thin proxy to the AgentField control plane.

Serves ``index.html`` and proxies ``/api/*`` to ``$AGENTFIELD_SERVER`` so the
browser makes SAME-ORIGIN calls (no CORS config needed on the control plane).
Mirrors the deep-research-ui deployment pattern (separate public Railway service
talking to ``control-plane.railway.internal:8080`` over the private network).
"""

from __future__ import annotations

import os

import requests
from flask import Flask, Response, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
CONTROL_PLANE = os.getenv(
    "AGENTFIELD_SERVER", "http://control-plane.railway.internal:8080"
).rstrip("/")
# The control plane gates /api/* behind an API key; inject it server-side so the
# key never reaches the browser (referenced from the control-plane service var).
API_KEY = os.getenv("AGENTFIELD_API_KEY", "")
PROXY_TIMEOUT_S = float(os.getenv("PROXY_TIMEOUT_S", "120"))
_HOP_BY_HOP = {"content-encoding", "content-length", "transfer-encoding", "connection"}

app = Flask(__name__, static_folder=None)


@app.get("/")
def index() -> Response:
    return send_from_directory(HERE, "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "control_plane": CONTROL_PLANE}


@app.route(
    "/api/<path:subpath>",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
def proxy(subpath: str) -> Response:
    """Pass every /api/* call straight through to the control plane."""
    headers = {k: v for k, v in request.headers if k.lower() != "host"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    upstream = requests.request(
        method=request.method,
        url=f"{CONTROL_PLANE}/api/{subpath}",
        params=request.args,
        data=request.get_data(),
        headers=headers,
        timeout=PROXY_TIMEOUT_S,
    )
    headers = [
        (k, v) for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    ]
    return Response(upstream.content, upstream.status_code, headers)


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8899")))


if __name__ == "__main__":
    main()
