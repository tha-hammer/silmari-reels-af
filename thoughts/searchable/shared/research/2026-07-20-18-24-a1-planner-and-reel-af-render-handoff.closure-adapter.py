"""Closure adapter (STAGED PROPOSAL — not wired into the repo).
Derived from the ClosureMap for: source + A1 plan artifacts -> delivered reel download_url.
Pin: 51fb8ec4d797cf651bea4b27d60b3c9880a4ab50.
Promote into silmari-reels-af and complete each TODO(promote) before use.
Speaks the 7-op contract apps/closure-oracle already talks to (mock_adapter.py).

Map (see the paired research doc):
  node0 SOURCE  a1_plan_artifacts   seedable = composite.ts.md + transcript.words.json + hook-plan.json
  node1 ENTRY   dsl_hooks_to_reels  (src/reel_af/app.py:1599)
  node2 OBSERVE reel_download_url   read_path = result["download_url"] (src/reel_af/app.py:1737)
  edges: 0->1 cross_boundary (object storage), 1->2 in-process. No async edge -> no /drive.
"""
import http.server, json, sys

ASYNC_EDGES = []                          # no async edge in this map
CONNECTOR = {e: True for e in ASYNC_EDGES}
SINK = []                                 # Phase-0 /seed_sink target


def handle(op, p):
    if op == "/reset":
        SINK.clear(); CONNECTOR.update({e: True for e in ASYNC_EDGES}); return {"ok": True}
    if op == "/set_connector":
        CONNECTOR[p["edge"]] = p["enabled"]; return {"ok": True}
    if op == "/seed_sink":
        SINK.append(p["value"]); return {"ok": True}
    if op == "/seed":
        # TODO(promote): materialize composite.ts.md + transcript.words.json + hook-plan.json
        # into a run dir (or object storage plans/{run_id}/) from p["data"].
        # Source-of-truth writers: src/reel_af/planner/serialize.py:145 (serialize_composite),
        # :170 (build_hook_plan); publisher src/reel_af/storage.py:179 (publish_a1_artifacts).
        return {"ok": True}
    if op == "/trigger":
        # TODO(promote): await reel_af.app.dsl_hooks_to_reels(
        #     source_url=p["args"]["source_url"],
        #     composite_ref=..., words_ref=..., hook_ref=...,
        #     clip_idx=p["args"].get("clip_idx", 1),
        # ) and capture result["download_url"].  Entrypoint: src/reel_af/app.py:1599.
        return {"ok": True}
    if op == "/drive":
        if not CONNECTOR.get(p["edge"], True):
            return {"ok": True}  # oracle disabled = red-at-seam
        # no async edge in this map
        return {"ok": True}
    if op == "/observe":
        # TODO(promote): return json.dumps(result["download_url"]) from the /trigger execution
        # (src/reel_af/app.py:1737); must be browser-deliverable per
        # _is_browser_deliverable_url (src/reel_af/app.py:1503).
        return {"ok": True, "value": json.dumps(SINK)}
    return {"ok": False, "error": "unknown op"}


class Hn(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        out = json.dumps(handle(self.path, json.loads(self.rfile.read(n) or "{}"))).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Hn).serve_forever()
