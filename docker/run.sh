#!/usr/bin/env bash
# docker/run.sh — bring up the full demo stack from pre-built images.
# Image registry + versions are parameterized via REGISTRY / APIM_BACKEND_VERSION /
# APIM_UI_VERSION / GAMMA_VERSION / AM_VERSION (defaults inlined in the compose
# files pull rolling tags from Gravitee's ACR; override in ./.env — see .env.example).
# The gamma modules / authz / MCP plugins are baked into the images, so there is
# nothing to stage — this is a thin wrapper over `docker compose up`.
#
# Usage:
#   run.sh                 pull + up
#   run.sh --no-pull       up using whatever images are already cached locally
#   run.sh setup           wait for AM + APIM to be healthy, then run setup.sh
#   run.sh down            docker compose down
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
# Optional overrides (image registry + versions). No .env is required;
# defaults are inlined in the compose files. See .env.example. A value already in
# the environment wins over .env, so `AM_VERSION=4.13.0 bash run.sh` overrides it.
if [ -f ./.env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key=${line%%=*}
    eval "isset=\${$key+x}"
    [ -z "$isset" ] && export "$key=${line#*=}"
  done < ./.env
fi

# wait_healthy <name> <url> — poll an endpoint until it answers (or give up).
wait_healthy(){
  local n=$1 u=$2 i=0
  while [ $i -lt 90 ]; do
    curl -fsS -o /dev/null --max-time 2 "$u" 2>/dev/null && { echo "  ✓ $n"; return 0; }
    sleep 2; i=$((i+1))
  done
  echo "  ✘ $n not ready (check: docker compose logs)" >&2; return 1
}

DO_PULL=1
case "${1:-}" in
  down)      exec docker compose down ;;
  --no-pull) DO_PULL=0 ;;
  setup)
    # Bootstrap the demo environment once AM + APIM answer. Assumes the stack is up.
    echo "── Waiting for AM + APIM + SPIRE ──"
    wait_healthy "AM mgmt API"   "http://localhost:8093/management/auth/login"
    wait_healthy "APIM rest-api" "http://localhost:8083/management/v2/ui/bootstrap"
    # SPIRE's JWKS must be servable before setup.sh registers the am.local trust domain.
    wait_healthy "SPIRE OIDC"    "http://localhost:18443/keys"
    echo "── Running setup.sh ──"
    exec bash "$HERE/setup.sh"
    ;;
  "")        ;;
  *) echo "unknown arg: $1" >&2; exit 1 ;;
esac

# ── Registry auth check ────────────────────────────────────────────────────────
# Default registry is the public Docker Hub (graviteeio/*). Override with
# REGISTRY=graviteeio.azurecr.io in ./.env to use the private ACR instead
# (requires `az acr login --name graviteeio`).
case "${REGISTRY:-graviteeio}" in
  *azurecr.io*)
    probe="${REGISTRY}/am-management-api:${AM_VERSION:-master-latest}"
    if ! docker manifest inspect "$probe" >/dev/null 2>&1; then
      echo "✘ cannot reach $probe — not logged in to graviteeio.azurecr.io, or the token has expired." >&2
      echo "  run:  az acr login --name graviteeio" >&2
      exit 1
    fi ;;
esac

# ── Port pre-flight ─────────────────────────────────────────────────────────
# `docker compose up` leaves containers created-but-not-started if a host port
# is already taken, needing a manual `down` + retry. Check the published ports
# from both compose files up front and bail with a clear message instead.
ports_in_use=""
for port in 80 8082 8083 8092 8093 9200 18081 18092 18093 18443 27017 27018 \
           8000 8001 8002 6274 6277 8087 6379 9002; do
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && ports_in_use="$ports_in_use $port"
done
if [ -n "$ports_in_use" ]; then
  echo "✘ required port(s) already in use:$ports_in_use" >&2
  echo "  free them (or stop the conflicting stack) and retry." >&2
  exit 1
fi

# ── Pull + up ──────────────────────────────────────────────────────────────────
if [ "$DO_PULL" = 1 ]; then
  echo "── docker compose pull ──"
  docker compose pull
fi
echo "── docker compose up -d --build ──"
# --build so locally-built services (gravitee-init, hotel-agent, hotel-mcp-server,
# acme-hotel-website, etc.) pick up source/API-def edits on a clean start;
# without it `up` reuses stale images and changes silently don't apply.
docker compose up -d --build

# ── Health poll ─────────────────────────────────────────────────────────────
# poll <name> <url> [host-header].  Backends are checked on their own ports;
# the UIs are checked through nginx (:80) with a Host header, since *.localhost
# may not resolve from the shell even though browsers special-case it.
echo "── Health poll ──"
poll(){
  local n=$1 u=$2 host=${3:-} i=0
  local hdr=()
  [ -n "$host" ] && hdr=(-H "Host: $host")
  while [ $i -lt 90 ]; do
    # ${hdr[@]+...} guards empty-array expansion under `set -u` (macOS bash 3.2).
    curl -fsS -o /dev/null --max-time 2 "${hdr[@]+"${hdr[@]}"}" "$u" 2>/dev/null \
      && { echo "  ✓ $n"; return 0; }
    sleep 2; i=$((i+1))
  done
  echo "  ✘ $n timed out (check: docker compose logs)"; return 1
}
# ── Gravitee backends (own host ports) ────────────────────────────────────
poll "AM mgmt API"   "http://localhost:8093/management/auth/login"        || true
poll "APIM rest-api" "http://localhost:8083/management/v2/ui/bootstrap"   || true
# UIs through the nginx proxy
poll "gamma console" "http://localhost/"  "gamma.localhost"               || true
poll "APIM console"  "http://localhost/"  "apim.localhost"                || true
poll "APIM portal"   "http://localhost/"  "portal.localhost"             || true
poll "AM webui"      "http://localhost/"  "am.localhost"                  || true
poll "SPIRE OIDC"    "http://localhost:18443/keys"                       || true
# ── Workshop services ────────────────────────────────────────────────────
poll "ACME Hotel API"     "http://localhost:8000/health"   || true
poll "Agent Live Graph"   "http://localhost:9002/"          || true

cat <<EOF

══════════════════════════════════════════════
Demo stack (docker, pre-built images) — UIs via nginx on :80, no port numbers:
  gamma console  http://gamma.localhost    admin/admin
  APIM console   http://apim.localhost     admin/admin
  APIM portal    http://portal.localhost
  AM webui       http://am.localhost       admin/adminadmin

Backends (direct):
  AM mgmt API    http://localhost:8093
  AM gateway     http://localhost:8092
  APIM rest-api  http://localhost:8083
  APIM gateway   http://localhost:8082
  SPIRE JWKS     http://localhost:18443/keys   (trust domain am.local)

Workshop services:
  ACME Hotel Website  http://localhost:8002
  Hotel Agent (A2A)   http://localhost:8001
  Agent Live Graph    http://localhost:9002
  ACME Hotel API      http://localhost:8000
  MCP Inspector       http://localhost:6274
  OpenFGA playground  http://localhost:3000
  Redis               redis://localhost:6379

One-shot setup (Gamma domain, Portal Next, AIM ↔ AM):
  bash docker/run.sh setup
(The workshop AM/APIM/OpenFGA bootstrap runs automatically via the gravitee-init container.)

Mint a JWT-SVID:  bash docker/spire/scripts/issue-svid.sh

If *.localhost doesn't resolve in your browser, add to /etc/hosts:
  127.0.0.1  gamma.localhost am.localhost apim.localhost portal.localhost
Stop:  bash docker/run.sh down
Logs:  cd docker && docker compose logs -f <service>
══════════════════════════════════════════════
EOF
