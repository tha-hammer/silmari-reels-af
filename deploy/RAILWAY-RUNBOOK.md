# reel-af — Railway deploy runbook

How to deploy (and re-deploy) the **reel-af render agent** + the **Cutting Room video-intake
UI** onto Railway, alongside the existing AgentField control plane. Written from a working
end-to-end deploy — the **Gotchas** section is the part that saves hours.

> **Source of truth is the repo** (Dockerfiles + `railway.toml` + `.railwayignore`). Railway
> holds only per-environment values and secrets.

---

## Status (2026-07-10)

✅ **Live and working end-to-end.** UI at https://reel-af-ui-production.up.railway.app; the
`reel-af` agent is registered (`discovery` shows it), and clicking **ROLL** submits a job that
the reasoner executes on the server. The registration-401 and the ROLL-403/CORS issues are
fixed (Gotchas #6 and #8).

⏳ **Active follow-up:** hardening video ingest — the YouTube path needs deno + cookies and the
generic format selector fails on non-YouTube hosts (Vimeo etc.). See `deploy/RAILWAY-RUNBOOK.md`
§7 and the ingest TDD plan under `thoughts/searchable/shared/plans/`.

---

## 1. What gets deployed

Two services, added to the **existing** `silmari-deep-research` Railway project (which already
runs `control-plane`, `deep-research-ui`, `Postgres`, `SuperTokens`):

| Service | Root dir | Dockerfile | Exposure | Port | Talks to |
|---|---|---|---|---|---|
| **reel-af** (render agent) | `silmari-reels-af/` | `Dockerfile` | private | 8002 | registers with `control-plane.railway.internal:8080` |
| **reel-af-ui** (Cutting Room) | `silmari-reels-af/web/` | `web/Dockerfile` | **public** | `$PORT` | proxies `/api/*` → `control-plane.railway.internal:8080` |

- **Live UI:** https://reel-af-ui-production.up.railway.app
- The UI is a Flask static-server + same-origin proxy (no CORS), mirroring `deep-research-ui`.
- The agent exposes `reel-af.reel_composite_to_reel` (video URL → preset reel), plus the
  generative `reel_article_to_reel` / `reel_topic_to_reel`.

---

## 2. Prerequisites

- **Account:** the `silmari-deep-research` project lives under **maceo.jourdan@gmail.com**
  (workspace *"Maceo's Projects"*) — **NOT** maceo@cosmicinc.ai (that account only has the
  Cosmic-HR project `believable-tranquility`; deploying there is wrong — no shared private net).
- **CLI:** `brew install railway` (user-writable, no sudo). Verify `railway whoami`.
- **Login:** `railway login` if `whoami` fails (interactive browser).
- **Link (once, from the agentfield meta-root):**
  ```bash
  cd ~/ntm_Dev/silmari-agentfield-system
  railway link --project 5dcbd074-f4f2-4284-b355-3e332d4538a5 --environment production
  ```

Project: `silmari-deep-research` = `5dcbd074-f4f2-4284-b355-3e332d4538a5`, env `production`.
`control-plane` service ID `8dd56fef-c6b0-4352-948d-98ee7c9fe707`.

---

## 3. Environment variables

Secrets live only in Railway. `AGENTFIELD_API_KEY` is **shared** across control-plane ↔ agent ↔ ui;
reference it from the control-plane service so the raw value is never copied.

### reel-af (agent)
| Var | Value |
|---|---|
| `AGENTFIELD_API_KEY` | `${{control-plane.AGENTFIELD_API_KEY}}` — **required for registration** (see Gotcha #6) |
| `AGENTFIELD_SERVER` | `http://control-plane.railway.internal:8080` |
| `AGENTFIELD_URL` | `http://control-plane.railway.internal:8080` |
| `AGENT_CALLBACK_URL` | `http://reel-af.railway.internal:8002` |
| `AGENT_NODE_ID` | `reel-af` |
| `PORT` | `8002` |
| `CHROMIUM_PATH` | `/usr/bin/chromium` |
| `OPENROUTER_API_KEY` | `${{silmari-deep-research.OPENROUTER_API_KEY}}` (only needed for article/topic; composite doesn't use it) |

### reel-af-ui
| Var | Value |
|---|---|
| `AGENTFIELD_SERVER` | `http://control-plane.railway.internal:8080` |
| `AGENTFIELD_API_KEY` | `${{control-plane.AGENTFIELD_API_KEY}}` — proxy injects it as `X-API-Key` |

Set with (example):
```bash
railway variables --service reel-af --set 'AGENTFIELD_API_KEY=${{control-plane.AGENTFIELD_API_KEY}}' --skip-deploys
```

---

## 4. Deploy

`silmari-reels-af` is a git repo **nested inside** the agentfield repo, so you must archive the
right directory explicitly with `--path-as-root` (see Gotcha #2). Run these from the agentfield
meta-root (the linked dir):

```bash
cd ~/ntm_Dev/silmari-agentfield-system

# First time only: create the services
railway add --service reel-af
railway add --service reel-af-ui
# …then set the env vars from §3 (with --skip-deploys), then:

# UI (light build ~1 min)
railway up silmari-reels-af/web --path-as-root --service reel-af-ui --detach
railway domain --service reel-af-ui          # create/confirm the public URL

# Agent (heavy build: node + chromium + npm ci, several minutes)
railway up silmari-reels-af --path-as-root --service reel-af --detach
```

Use `--ci` instead of `--detach` to stream build logs and get an exit code, or poll:
```bash
railway deployment list --service reel-af --json | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["status"])'
# BUILDING → SUCCESS (deployed) | FAILED (triage §6)
```
**Never report a deploy done until you see `SUCCESS`** (a detached `up` only confirms the build
started).

---

## 5. Verify (end-to-end)

```bash
# 1. UI is up
curl -s https://reel-af-ui-production.up.railway.app/health        # {"status":"ok",...}

# 2. Agent registered with the control plane
curl -s https://reel-af-ui-production.up.railway.app/api/v1/discovery/capabilities \
  | python3 -c 'import sys,json;print([c["agent_id"] for c in json.load(sys.stdin)["capabilities"]])'
# expect: ['reel-af', 'meta_deep_research']
railway logs --service reel-af | grep -E 'heartbeat|401'            # want "heartbeat ... ready", no 401

# 3. Submit a job through the public UI (non-YouTube URL — see Limitations)
curl -s -X POST https://reel-af-ui-production.up.railway.app/api/v1/execute/async/reel-af.reel_composite_to_reel \
  -H 'Content-Type: application/json' \
  -d '{"input":{"url":"<video-url>","preset":"middle-third-dynamic","count":1}}'
# → 202 + execution_id; poll GET /api/v1/executions/{id} until status=succeeded
```

---

## 6. Gotchas & troubleshooting (each cost real debugging time)

1. **Wrong account/project.** `silmari-deep-research` is under `maceo.jourdan@gmail.com`. If
   `railway project list` shows only `believable-tranquility`, you're on the wrong account — that's
   Cosmic-HR. Deploying reel-af there breaks the private network (`control-plane.railway.internal`
   unreachable). `railway logout && railway login` with the right account.

2. **Nested repo → wrong upload.** `railway up` from a nested repo uploads the **outer** repo (the
   build fails with Railpack analyzing the agentfield meta-root, not your files). Always
   `railway up <path> --path-as-root --service <svc>`. Ship a `.railwayignore` at the reels root
   (exclude `node_modules/.venv/output`) or the context is ~500 MB and the build dies.

3. **Metal builder rejects BuildKit-only Dockerfile features.** `RUN --mount=type=cache …` and
   `COPY --from=<remote image>` make the Railway Metal builder fail at *schedule* time with **zero
   build logs** ("scheduling build on Metal builder" → "Deploy failed"). Use plain Docker:
   `pip install .`, no cache mounts, no multi-stage remote COPY. (`docker build --check .` passing
   locally does NOT catch this — it's builder-side.)

4. **Pin the SDK.** `pip install agentfield` pulls latest; pin `agentfield==0.1.96` in
   `pyproject.toml` to match the deployed control plane + sibling agents. (Mismatch is not the
   registration 401 — see #6 — but keep them in lockstep.)

5. **Missing render deps.** The image needs `yt-dlp` (ingest) + `uv` (whisper via `uvx
   whisper-ctranslate2`) on top of `ffmpeg` + `nodejs` + `chromium`. Installed via
   `pip install yt-dlp uv` and apt.

6. **Registration 401 → `agent 'reel-af' not found`.** The **root cause of the whole mesh not
   working.** The Python SDK only sends the `X-API-Key` registration header when the **`Agent()`
   constructor** gets `api_key=` (SDK `verification.py`). Setting the key only inside `AIConfig`
   (the *LLM* key) is not enough. Required in `app.py`:
   ```python
   app = Agent(..., api_key=os.getenv("AGENTFIELD_API_KEY"), ...)
   ```
   Symptom: agent healthchecks green but logs `Fast lifecycle registration failed with status 401`
   + `memory events … HTTP 401`, and discovery omits the agent. The control plane's `APIKeyAuth`
   (`auth.go`) compares `X-API-Key`/`Bearer`/`?api_key=` against `AGENTFIELD_API_KEY`.

7. **UI job submit 401.** The control plane gates `/api/*` behind the API key. `web/server.py`
   injects `X-API-Key` from `AGENTFIELD_API_KEY` server-side so the key never reaches the browser.

8. **ROLL returns 403 "rejected" (but curl works).** The control plane's CORS allowlist is
   `localhost:3000/5173`; when the proxy forwards the **browser's `Origin` header**, the plane
   sees a disallowed cross-origin call → 403. The proxy is a server-to-server client, so
   `web/server.py` must **strip `Origin`/`Referer`** from forwarded requests (`_STRIP_REQUEST`).
   Tell: a bare curl (no `Origin`) gets 202, the browser gets 403.

---

## 7. Known limitations / follow-ups

- **YouTube ingest fails on the headless server.** yt-dlp needs **deno** (JS runtime — *"No
  supported JavaScript runtime"*) **and cookies** (*"Sign in to confirm you're not a bot"*). Add
  deno + `--js-runtimes deno` + a mounted cookies file to `download_crisp_source`
  (`render/hooks.py`). Non-gated video URLs work today. (bead `A1_workspace-blueprint-gm9`)
- **Rendered-file retrieval.** A finished reel lands on the agent container's filesystem; the
  browser can't download it without object storage or a file-serving route. Not yet wired.
- **Drop-file upload.** The UI's file-drop needs a multipart upload route on the Go control plane;
  URL mode is the working path today.
- **Open access.** The public UI can trigger jobs unauthenticated. Add a login gate (deep-research-ui
  uses SuperTokens; or HTTP basic) to restrict it.
- **Overlay coverage.** `reel_composite_to_reel` wires the `middle_third` overlay; the
  `lower_third` / horizontal path is a follow-up (bead `A1_workspace-blueprint-t3u`).
