"""Closure adapter (STAGED PROPOSAL - not wired into the repo).

Derived from the ClosureMap in:
2026-07-11-12-59-reels-af-ui-configuration-options.md

Behavior: finish_reel returns a polished final.mp4 with banner, captions, and
image cut-ins burned through the production ffmpeg path.

Pin: dfdd98931a4aab3b065baf337f84aecc1f7e5bec.
Promote into /home/maceo/ntm_Dev/silmari-agentfield-system/silmari-reels-af
and complete each TODO(promote) before use.
"""
import http.server
import json
import sys

ASYNC_EDGES = []
CONNECTOR = {edge: True for edge in ASYNC_EDGES}
SINK = []
STATE = {}


def handle(op, payload):
    if op == "/reset":
        SINK.clear()
        STATE.clear()
        CONNECTOR.update({edge: True for edge in ASYNC_EDGES})
        return {"ok": True}
    if op == "/set_connector":
        CONNECTOR[payload["edge"]] = payload["enabled"]
        return {"ok": True}
    if op == "/seed_sink":
        SINK.append(payload["value"])
        return {"ok": True}
    if op == "/seed":
        # TODO(promote): seed base Path + FinishContext + ReelFinishConfig (src/reel_af/render/finish.py:54, src/reel_af/render/finish_config.py:77, src/reel_af/render/finish.py:204)
        STATE["seed"] = payload["data"]
        return {"ok": True}
    if op == "/trigger":
        # TODO(promote): call finish_reel(base, ctx, cfg, deps=..., out_dir=...) (src/reel_af/render/finish.py:204)
        return {"ok": True}
    if op == "/drive":
        return {"ok": True}
    if op == "/observe":
        # TODO(promote): return json.dumps(probe_duration(final_mp4)) (src/reel_af/render/finish.py:284)
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
