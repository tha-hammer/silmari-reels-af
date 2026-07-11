# reel-af — Railway deployment

Canonical guide for deploying the two reel-af services to Railway, plus the shared
auth + user-data layer they depend on. Written from a working end-to-end deploy.

- **Automated path:** `deploy/deploy.sh` (see [§7](#7-deploy-script)) wraps every command below.
- **Deep troubleshooting log:** `deploy/RAILWAY-RUNBOOK.md` (per-gotcha debugging history).
- **Source of truth is the repo** (Dockerfiles + `railway.toml` + `.railwayignore`). Railway holds
  only per-environment values and secrets.

---

## 1. Architecture — what gets deployed

Two services are added to the **existing** `silmari-deep-research` Railway project (which already
runs `control-plane`, `deep-research-ui`, `SuperTokens`, and two Postgres instances — `Postgres`
and `user_data`):

| Service | Repo / Root dir | Dockerfile | Exposure | Port | Role |
|---|---|---|---|---|---|
| **reel-af** (render agent) | `silmari-reels-af/` → root `/` | `Dockerfile` | private | 8002 | registers with `control-plane.railway.internal:8080`, runs the reasoner DAG |
| **reel-af-ui** (Cutting Room) | `silmari-reels-af/` → root `web/` | `web/Dockerfile` | **public** | `$PORT` | authenticated ownership boundary → dispatches to the control plane |

- **Live UI:** https://reel-af-ui-production.up.railway.app
- The **agent** deploys from the repo root (`Dockerfile` copies `pyproject.toml` + `src/`; it does
  **not** build from `/src` — that has no build config). The **UI** deploys from `web/`.
- The UI is no longer a bare proxy: it verifies a SuperTokens session, resolves org/role from the
  shared `deepresearch` schema, stamps a `deepresearch.reel_job` ownership row, then dispatches an
  identity-free body to the control plane. The agent + control plane stay identity-free.

```
browser ──(session)──▶ reel-af-ui (auth boundary) ──(X-API-Key)──▶ control-plane ──▶ reel-af agent
                            │                                             (identity-free)
                            ├─ SuperTokens core (shared)  ── session verify + /auth/*
                            └─ user_data Postgres (deepresearch schema)  ── membership + reel_job
```

---

## 2. Prerequisites

- **Account:** the `silmari-deep-research` project lives under **maceo.jourdan@gmail.com**
  (workspace *"Maceo's Projects"*). NOT maceo@cosmicinc.ai — that account lacks the shared private
  network, so `*.railway.internal` is unreachable there.
- **CLI:** `railway` (v5+). Verify with `railway whoami`; `railway login` if it fails.
- **Link once** (from the meta-repo root, which is the deploy working dir):
  ```bash
  cd ~/ntm_Dev/silmari-agentfield-system
  railway link --project 5dcbd074-f4f2-4284-b355-3e332d4538a5 --environment production
  ```
- **Local Docker** (optional, to pre-flight the Dockerfiles before Railway): Docker daemon running.
- IDs: project `5dcbd074-f4f2-4284-b355-3e332d4538a5`, env `production`,
  control-plane `8dd56fef-c6b0-4352-948d-98ee7c9fe707`,
  user_data `f9b7f90b-609c-4dc8-b191-529f343489e9`.

---

## 3. Environment variables

Secrets live only in Railway. Set with `--skip-deploys` so setting a var doesn't trigger an
intermediate build; deploy the code afterward.

### reel-af (agent)
| Var | Value |
|---|---|
| `AGENTFIELD_API_KEY` | `${{control-plane.AGENTFIELD_API_KEY}}` — **required for registration** (Gotcha #6) |
| `AGENTFIELD_SERVER` / `AGENTFIELD_URL` | `http://control-plane.railway.internal:8080` |
| `AGENT_CALLBACK_URL` | `http://reel-af.railway.internal:8002` |
| `AGENT_NODE_ID` | `reel-af` |
| `PORT` | `8002` |
| `CHROMIUM_PATH` | `/usr/bin/chromium` |
| `OPENROUTER_API_KEY` | `${{silmari-deep-research.OPENROUTER_API_KEY}}` |

### reel-af-ui (auth boundary)
| Var | Value | Purpose |
|---|---|---|
| `AGENTFIELD_SERVER` | `http://control-plane.railway.internal:8080` | dispatch target |
| `AGENTFIELD_API_KEY` | `${{control-plane.AGENTFIELD_API_KEY}}` | injected as `X-API-Key` server-side |
| `SUPERTOKENS_CONNECTION_URI` | `http://supertokens.railway.internal:3567` | shared SuperTokens core |
| `SUPERTOKENS_API_KEY` | `${{SuperTokens.API_KEYS}}` (or the literal core key) | core auth |
| `DEEPRESEARCH_DATABASE_URL` | `${{user_data.DATABASE_URL}}` | shared `deepresearch` schema |
| `UI_WEBSITE_DOMAIN` | `https://reel-af-ui-production.up.railway.app` | cookie/CORS domain (its OWN domain) |
| `REEL_DEFAULT_ORG_ID` | `e4e47131-cd9f-4882-9925-194e9db062ca` | default org (shared with deep-research) |
| `REEL_OWNER_EMAILS` | `maceo.jourdan@gmail.com` | JIT-bootstrapped as `owner`; others → `member` |
| `REEL_ALLOWED_EMAILS` | `maceo.jourdan@gmail.com` | signup allowlist (empty = open registration) |
| `REEL_UPLOAD_DIR` | *(unset)* | file uploads 503 until a volume is mounted here; URL/topic work without it |

Example:
```bash
railway variables --service reel-af-ui --skip-deploys \
  --set 'SUPERTOKENS_CONNECTION_URI=http://supertokens.railway.internal:3567' \
  --set 'DEEPRESEARCH_DATABASE_URL=${{user_data.DATABASE_URL}}' \
  --set 'UI_WEBSITE_DOMAIN=https://reel-af-ui-production.up.railway.app'
```

---

## 4. Database migrations (shared user-data schema)

The UI's ownership + tenancy tables live in the **root-owned** `deepresearch` schema on the
Railway **`user_data`** Postgres — reel-af **consumes** them, it never vendors them. Migration
files: `migrations/deepresearch/*.sql` at the **meta-repo root** (dbmate-style `-- migrate:up` /
`-- migrate:down`). Additive + idempotent (`create table if not exists`, `insert ... on conflict do
nothing`), so they're safe alongside deep-research's existing `research_run`.

| # | Table |
|---|---|
| 100–103 | `organization`, `user`, `membership`, `role_definition` |
| 106 | seed: default org (`e4e47131-…`) + `owner/admin/member/viewer` role→permission matrix |
| 108 | `reel_job` (FKs to org/user/research_run; unique `(org_id, created_by, client_request_id)`) |

Apply them (idempotent — safe to re-run): `deploy/deploy.sh migrate` (see §7), or manually against
`user_data`'s public URL. **Until applied, the UI fails closed with `503` and makes no control-plane
call** — that is the intended behavior, not a bug.

---

## 5. Auth (SuperTokens + JIT bootstrap)

- **Recipe:** emailpassword + session, mounted at `/auth/*`, mirroring `deep-research-ui`. Same
  shared core; each UI uses its **own** `UI_WEBSITE_DOMAIN` for cookies (they are separate Railway
  subdomains, so a shared parent-domain cookie is not possible — reels owns its login).
- **Login:** `web/login.html` drives the SuperTokens FDI endpoints directly (no JS bundle). `GET /`
  redirects unauthenticated users to `/login`.
- **First owner:** register at `/login` with an email in `REEL_ALLOWED_EMAILS`. On the first
  authenticated request, the JIT bootstrap inserts the SuperTokens user into `deepresearch.user` +
  `membership` (default org), assigning `owner` to `REEL_OWNER_EMAILS`, else `member`.
- **Fail-closed everywhere:** no session → 401; unresolved membership → 403; schema/DB down → 503;
  forged `org_id`/`created_by` in the body → 400. None of these reach the control plane.

---

## 6. Deploy

Two equivalent paths. **Always confirm `SUCCESS`** before calling it done — a detached `up` (or a
git push) only confirms the build *started*.

### A. git push (auto-deploy)
`reel-af` and `reel-af-ui` auto-deploy on push to their watched branch (`main` as of the
2026-07-11 untangle; both services were repointed from `feat/reel-intake-ui` to `main`), building
from their respective root dirs (`/` and `web/`). Push, then poll:
```bash
git push origin main
railway deployment list --service reel-af-ui --json | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["status"])'
```

### B. railway up (explicit)
`silmari-reels-af` is a git repo **nested inside** the meta-repo, so you MUST pass the path with
`--path-as-root` (Gotcha #2) — otherwise `railway up` uploads the outer repo and the build fails
with Railpack analyzing the wrong files. Run from the meta-repo root:
```bash
cd ~/ntm_Dev/silmari-agentfield-system
railway up silmari-reels-af/web --path-as-root --service reel-af-ui --ci   # UI (~1 min)
railway up silmari-reels-af     --path-as-root --service reel-af    --ci   # agent (heavy, minutes)
```

Both are wrapped by `deploy/deploy.sh` (§7).

---

## 7. Deploy script

`deploy/deploy.sh` automates build, migrate, deploy, and verify. Run it from anywhere.

```bash
deploy/deploy.sh build [ui|agent|all]   # docker build locally to pre-flight the Dockerfiles
deploy/deploy.sh migrate                # apply migrations/deepresearch to the user_data DB (idempotent)
deploy/deploy.sh deploy [ui|agent|all]  # railway up --path-as-root, wait for SUCCESS
deploy/deploy.sh verify                 # health + auth-boundary + discovery probes
deploy/deploy.sh local                  # docker compose up (local stack: control-plane + agent)
deploy/deploy.sh all                    # migrate → deploy all → verify
```

---

## 8. Verify (end-to-end)

```bash
B=https://reel-af-ui-production.up.railway.app
curl -s -o /dev/null -w '%{http_code}\n' $B/health                 # 200
curl -s -o /dev/null -w '%{http_code}\n' $B/                       # 302 -> /login  (unauth, fail-closed)
curl -s -o /dev/null -w '%{http_code}\n' $B/login                  # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  $B/api/v1/execute/async/reel-af.reel_topic_to_reel \
  -H 'Content-Type: application/json' -d '{"input":{"topic":"x"}}' # 401 (no session)
curl -s -o /dev/null -w '%{http_code}\n' "$B/auth/signup/email/exists?email=x@y.z" -H 'rid: emailpassword'  # 200 (SuperTokens up)
# agent registered:
railway logs --service reel-af | grep -E 'heartbeat|401'           # want "heartbeat ready", no 401
```

After signing in, a `ROLL` submit should return `202` + `execution_id`, write a `deepresearch.reel_job`
row, and reconcile on poll.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **"scheduling build on Metal builder" → Deploy failed, ZERO build logs** | Dockerfile uses a BuildKit-only feature the Metal builder rejects at schedule time: `RUN --mount=type=cache …` or `COPY --from=<remote image>`. `docker build` passing locally does NOT catch this. | Use plain Docker (`pip install .`, no cache mounts, no multi-stage remote COPY). This is the #1 cause of a silent agent-build failure. |
| Build fails, Railpack lists the **meta-repo** files (`migrations/`, `manifest.yaml`) | `railway up` from the nested repo uploaded the outer repo | `railway up <path> --path-as-root --service <svc>` |
| Context ~500 MB / build dies | `node_modules`/`.venv`/`output` uploaded | ensure `.railwayignore` at the reels root excludes them |
| Agent healthy but `discovery` omits it; logs `registration failed 401` | SDK only sends `X-API-Key` when `Agent(api_key=…)` is set (not via `AIConfig`) | set `api_key=os.getenv("AGENTFIELD_API_KEY")` in `app.py`; set the var to `${{control-plane.AGENTFIELD_API_KEY}}` |
| UI submit works via curl, browser gets **403 "rejected"** | proxy forwarded the browser `Origin` → control-plane CORS allowlist rejects | `web/server.py`/CP client strips `Origin`/`Referer` (server-to-server) |
| UI returns **503** on every protected route | `DEEPRESEARCH_DATABASE_URL` unset or `deepresearch` schema not applied | apply §4 migrations; set the DB var |
| Everyone gets **403** after login | `deepresearch.membership`/`role_definition` empty | ensure migration 106 seeded roles + org; the JIT bootstrap needs the default org to exist |
| Deploy to wrong project / `*.railway.internal` unreachable | linked to the Cosmic-HR account | `railway logout && railway login` as maceo.jourdan@gmail.com; re-link the project |

---

## 10. Known limitations

- **File uploads** need a Railway volume mounted at `REEL_UPLOAD_DIR`; URL + topic modes work now.
- **Rendered-file retrieval** from the agent container needs object storage or a file-serving route.
- **YouTube ingest** needs a runtime `cookies.txt` (`YTDLP_COOKIES_FILE`) — see
  `deploy/RAILWAY-RUNBOOK.md` §7. Vimeo/generic hosts work without it.
