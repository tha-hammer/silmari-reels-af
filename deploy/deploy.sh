#!/usr/bin/env bash
# reel-af Docker/Railway deploy script.
#
# Wraps the proven deploy flow from docs/railway-deployment.md: pre-flight the
# Dockerfiles locally, apply the shared deepresearch migrations, deploy both
# services to Railway (nested-repo aware via --path-as-root), and verify.
#
#   deploy/deploy.sh build   [ui|agent|all]   docker build locally (pre-flight the Dockerfiles)
#   deploy/deploy.sh setvars                   set reel-af-ui auth env vars (idempotent, --skip-deploys)
#   deploy/deploy.sh migrate                   apply migrations/deepresearch to the user_data DB
#   deploy/deploy.sh deploy  [ui|agent|all]    railway up --path-as-root, wait for SUCCESS
#   deploy/deploy.sh verify                     health + auth-boundary + discovery probes
#   deploy/deploy.sh local                      docker compose up (local control-plane + agent)
#   deploy/deploy.sh all                        migrate -> deploy all -> verify
#
set -euo pipefail

# ── paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REELS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"            # silmari-reels-af (this repo)
META_ROOT="${META_ROOT:-$(cd "$REELS_DIR/.." && pwd)}"  # silmari-agentfield-system (linked dir)
REELS_REL="$(basename "$REELS_DIR")"                # path segment for `railway up`
MIGRATIONS_DIR="${MIGRATIONS_DIR:-$META_ROOT/migrations/deepresearch}"

# ── config ───────────────────────────────────────────────────────────────────
PROJECT_ID="5dcbd074-f4f2-4284-b355-3e332d4538a5"
RW_ENV="production"
UI_SERVICE="reel-af-ui"
AGENT_SERVICE="reel-af"
USER_DATA_SERVICE="user_data"
PUBLIC_URL="https://reel-af-ui-production.up.railway.app"
DEFAULT_ORG_ID="e4e47131-cd9f-4882-9925-194e9db062ca"
OWNER_EMAILS="maceo.jourdan@gmail.com"

# ── logging ──────────────────────────────────────────────────────────────────
c_blue=$'\033[34m'; c_grn=$'\033[32m'; c_red=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '%s▶ %s%s\n' "$c_blue" "$*" "$c_off"; }
ok()   { printf '%s✓ %s%s\n' "$c_grn" "$*" "$c_off"; }
err()  { printf '%s✖ %s%s\n' "$c_red" "$*" "$c_off" >&2; }
die()  { err "$*"; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

railway_preflight() {
  need railway
  railway whoami >/dev/null 2>&1 || die "not logged in — run: railway login"
  # Warn (don't fail) if the linked project isn't the expected one.
  if ! railway status 2>/dev/null | grep -q "$PROJECT_ID"; then
    err "Railway project not linked to $PROJECT_ID."
    err "  cd $META_ROOT && railway link --project $PROJECT_ID --environment $RW_ENV"
    die "link the project, then re-run."
  fi
}

# ── docker build (local pre-flight) ──────────────────────────────────────────
build_agent() {
  need docker
  log "docker build agent (context: repo root, Dockerfile)"
  docker build -t reel-af:local -f "$REELS_DIR/Dockerfile" "$REELS_DIR"
  ok "reel-af:local built"
}
build_ui() {
  need docker
  log "docker build UI (context: web/, web/Dockerfile)"
  docker build -t reel-af-ui:local -f "$REELS_DIR/web/Dockerfile" "$REELS_DIR/web"
  ok "reel-af-ui:local built"
}

# ── migrations (shared user_data DB) ─────────────────────────────────────────
db_url() {
  railway_preflight
  railway variables --service "$USER_DATA_SERVICE" --environment "$RW_ENV" --json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("DATABASE_PUBLIC_URL",""))'
}
migrate() {
  [ -d "$MIGRATIONS_DIR" ] || die "migrations dir not found: $MIGRATIONS_DIR (set META_ROOT or MIGRATIONS_DIR)"
  local url; url="$(db_url)"
  [ -n "$url" ] || die "could not resolve user_data DATABASE_PUBLIC_URL from Railway"
  log "applying migrations from $MIGRATIONS_DIR (idempotent)"
  local runner
  if command -v psql >/dev/null 2>&1; then runner=psql
  elif command -v uv >/dev/null 2>&1; then runner=uv
  else die "need psql or uv to apply migrations"; fi

  for f in "$MIGRATIONS_DIR"/[0-9]*.sql; do
    [ -e "$f" ] || die "no .sql files in $MIGRATIONS_DIR"
    # extract the `-- migrate:up` section (up to `-- migrate:down`)
    local up; up="$(sed -n '/-- migrate:up/,/-- migrate:down/p' "$f" | sed '1d;$d')"
    if [ "$runner" = psql ]; then
      printf '%s\n' "$up" | psql "$url" -v ON_ERROR_STOP=1 -q
    else
      UP_SQL="$up" DB_URL="$url" uv --directory "$REELS_DIR" run --quiet python - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["DB_URL"], connect_timeout=15) as c:
    c.execute(os.environ["UP_SQL"]); c.commit()
PY
    fi
    ok "applied $(basename "$f")"
  done
  ok "migrations complete"
}

# ── env vars (reel-af-ui auth) ───────────────────────────────────────────────
setvars() {
  railway_preflight
  log "setting reel-af-ui auth vars (--skip-deploys)"
  railway variables --service "$UI_SERVICE" --environment "$RW_ENV" --skip-deploys \
    --set 'SUPERTOKENS_CONNECTION_URI=http://supertokens.railway.internal:3567' \
    --set 'SUPERTOKENS_API_KEY=${{SuperTokens.API_KEYS}}' \
    --set 'DEEPRESEARCH_DATABASE_URL=${{user_data.DATABASE_URL}}' \
    --set "UI_WEBSITE_DOMAIN=$PUBLIC_URL" \
    --set "REEL_DEFAULT_ORG_ID=$DEFAULT_ORG_ID" \
    --set "REEL_OWNER_EMAILS=$OWNER_EMAILS" \
    --set "REEL_ALLOWED_EMAILS=$OWNER_EMAILS"
  ok "vars set"
}

# ── deploy (railway up, nested-repo aware) ───────────────────────────────────
wait_success() {
  local svc="$1" i status
  for i in $(seq 1 40); do
    status="$(railway deployment list --service "$svc" --environment "$RW_ENV" --json 2>/dev/null \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["status"] if d else "NONE")')"
    printf '  %s[%3ds] %s: %s%s\n' "$c_dim" "$((i*15))" "$svc" "$status" "$c_off"
    case "$status" in
      SUCCESS) ok "$svc deployed"; return 0 ;;
      FAILED|CRASHED) die "$svc deploy $status — triage docs/railway-deployment.md §9 (empty logs ⇒ BuildKit/Metal builder)" ;;
    esac
    sleep 15
  done
  die "$svc deploy did not reach SUCCESS in time"
}
deploy_ui() {
  railway_preflight
  log "railway up UI ($REELS_REL/web --path-as-root)"
  ( cd "$META_ROOT" && railway up "$REELS_REL/web" --path-as-root --service "$UI_SERVICE" --detach )
  wait_success "$UI_SERVICE"
}
deploy_agent() {
  railway_preflight
  log "railway up agent ($REELS_REL --path-as-root, heavy build)"
  ( cd "$META_ROOT" && railway up "$REELS_REL" --path-as-root --service "$AGENT_SERVICE" --detach )
  wait_success "$AGENT_SERVICE"
}

# ── verify ───────────────────────────────────────────────────────────────────
probe() { # method path expected-code description
  local code; code="$(curl -s -o /dev/null -w '%{http_code}' -X "$1" "$PUBLIC_URL$2" "${@:5}")"
  if [ "$code" = "$3" ]; then ok "$4 ($1 $2 → $code)"; else err "$4 EXPECTED $3 GOT $code ($1 $2)"; return 1; fi
}
verify() {
  need curl
  log "verifying $PUBLIC_URL"
  local fail=0
  probe GET  /health 200 "health"                                              || fail=1
  probe GET  /       302 "unauth root redirects to login"                      || fail=1
  probe GET  /login  200 "login page"                                          || fail=1
  probe POST /api/v1/execute/async/reel-af.reel_topic_to_reel 401 "unauth submit is fail-closed" \
    -H 'Content-Type: application/json' -d '{"input":{"topic":"x"}}'           || fail=1
  probe GET  "/auth/signup/email/exists?email=x@y.z" 200 "SuperTokens mounted" -H 'rid: emailpassword' || fail=1
  probe GET  /api/v1/whoami 404 "unknown /api is 404 (no open proxy)"          || fail=1
  [ "$fail" = 0 ] && ok "all probes passed" || die "verification failed"
}

# ── local docker-compose ─────────────────────────────────────────────────────
local_up() {
  need docker
  log "docker compose up (local stack)"
  ( cd "$REELS_DIR" && docker compose up --build )
}

# ── dispatch ─────────────────────────────────────────────────────────────────
target="${2:-all}"
case "${1:-}" in
  build)   case "$target" in ui) build_ui ;; agent) build_agent ;; all|"") build_agent; build_ui ;; *) die "build [ui|agent|all]" ;; esac ;;
  setvars) setvars ;;
  migrate) migrate ;;
  deploy)  case "$target" in ui) deploy_ui ;; agent) deploy_agent ;; all|"") deploy_ui; deploy_agent ;; *) die "deploy [ui|agent|all]" ;; esac ;;
  verify)  verify ;;
  local)   local_up ;;
  all)     migrate; deploy_ui; deploy_agent; verify ;;
  *) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
