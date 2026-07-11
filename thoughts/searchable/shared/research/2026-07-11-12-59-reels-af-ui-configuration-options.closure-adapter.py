"""Closure adapter (STAGED PROPOSAL - not wired into the repo).

Derived from the ClosureMap in:
2026-07-11-12-59-reels-af-ui-configuration-options.md

Pin: 18ea51fbb6923061910706c3936dc020d83b2444.
Promote into /home/maceo/ntm_Dev/silmari-agentfield-system/silmari-reels-af
and complete each TODO(promote) before use.
"""
import http.server
import json
import sys

ASYNC_EDGES = ["reel_job_row_and_dispatch->agentfield_execution"]
CONNECTOR = {edge: True for edge in ASYNC_EDGES}
SINK = []


def handle(op, payload):
    if op == "/reset":
        SINK.clear()
        CONNECTOR.update({edge: True for edge in ASYNC_EDGES})
        return {"ok": True}
    if op == "/set_connector":
        CONNECTOR[payload["edge"]] = payload["enabled"]
        return {"ok": True}
    if op == "/seed_sink":
        SINK.append(payload["value"])
        return {"ok": True}
    if op == "/seed":
        # TODO(promote): seed web/index.html#config+state with payload["data"] (web/index.html:12, web/index.html:389)
        return {"ok": True}
    if op == "/trigger":
        # TODO(promote): call roll()/buildInput()/execute(payload["args"]) (web/index.html:515, web/index.html:527, web/index.html:576)
        return {"ok": True}
    if op == "/drive":
        if not CONNECTOR.get(payload["edge"], True):
            return {"ok": True}
        # TODO(promote): drain CP execution and browser poll via poll() (web/index.html:600)
        return {"ok": True}
    if op == "/observe":
        # TODO(promote): return json.dumps(finish/poll observed result) (web/index.html:627)
        return {"ok": True, "value": json.dumps(SINK)}
    return {"ok": False, "error": "unknown op"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n_bytes = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n_bytes) or b"{}"
        out = json.dumps(handle(self.path, json.loads(raw))).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_args):
        pass


http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
