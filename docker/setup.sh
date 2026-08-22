#!/usr/bin/env bash
# docker/setup.sh — prepare the demo environment via the AM + APIM management APIs.
#
# Pure curl, no extra tooling. Run once AM + APIM are up — `run.sh setup` health-gates
# and invokes this. Safe to re-run: everything is idempotent (look up by name first).
#
# Auth:
#   AM   — basic admin:adminadmin -> POST /management/auth/token -> short-lived bearer
#          (enough for a one-shot bootstrap; no PAT lifecycle to manage).
#   APIM — basic admin:admin works directly on the v2 management API.
#
# Edit the "RECIPE" section to add the domains / apps / config your demo needs.
set -euo pipefail

AM_URL=${AM_URL:-http://localhost:8093}
APIM_URL=${APIM_URL:-http://localhost:8083}
AM_CREDS=${AM_CREDS:-admin:adminadmin}
APIM_CREDS=${APIM_CREDS:-admin:admin}
ORG=${ORG:-DEFAULT}
ENV=${ENV:-DEFAULT}

py() { python3 -c "$1"; }  # tiny JSON helper — avoids a jq dependency

# ── AM auth + helpers ─────────────────────────────────────────────────────────
echo "── AM: minting admin token ──"
AM_TOKEN=$(curl -fsS -u "$AM_CREDS" -X POST "$AM_URL/management/auth/token" \
  | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')
am() { curl -fsS -H "Authorization: Bearer $AM_TOKEN" -H 'Content-Type: application/json' "$@"; }
AM_ORG="$AM_URL/management/organizations/$ORG"
AM_DOMAINS="$AM_ORG/environments/$ENV/domains"

# create + enable a security domain, idempotent by name. echoes the domain id.
am_domain() { # <name> [description]
  local name=$1 desc=${2:-}
  local id
  id=$(am "$AM_DOMAINS?size=100" | py "import sys,json
print(next((d['id'] for d in json.load(sys.stdin).get('data',[]) if d['name']=='$name'),''))")
  if [ -n "$id" ]; then
    echo "  = domain '$name' exists ($id)" >&2
  else
    id=$(am -X POST "$AM_DOMAINS" \
      -d "{\"name\":\"$name\",\"description\":\"$desc\",\"dataPlaneId\":\"default\"}" \
      | py 'import sys,json;print(json.load(sys.stdin)["id"])')
    echo "  + created domain '$name' ($id)" >&2
  fi
  am -X PATCH "$AM_DOMAINS/$id" -d '{"enabled":true}' >/dev/null
  echo "$id"
}

# resolve the auto-provisioned default inline IdP for a domain. echoes its id.
am_default_idp() { # <domain-id>
  am "$AM_DOMAINS/$1/identities" | py 'import sys,json
items=json.load(sys.stdin); print(items[0]["id"] if items else "")'
}

# create an application, idempotent by name. echoes the app id.
am_app() { # <domain-id> <name> <type> <redirect-uris-json-array>
  local dom=$1 name=$2 type=$3 redirects=$4
  local base="$AM_DOMAINS/$dom/applications" id
  id=$(am "$base?size=100" | py "import sys,json
print(next((a['id'] for a in json.load(sys.stdin).get('data',[]) if a['name']=='$name'),''))")
  if [ -n "$id" ]; then
    echo "  = app '$name' exists ($id)" >&2
  else
    id=$(am -X POST "$base" \
      -d "{\"name\":\"$name\",\"type\":\"$type\",\"redirectUris\":$redirects}" \
      | py 'import sys,json;print(json.load(sys.stdin)["id"])')
    echo "  + created app '$name' ($id)" >&2
  fi
  echo "$id"
}

# register a SPIFFE trust domain (JWKS-backed), idempotent by name.
am_trust_domain() { # <domain-id> <name> <jwks-url>
  local dom=$1 name=$2 jwks=$3
  local base="$AM_DOMAINS/$dom/trust-domains" id
  id=$(am "$base" | py "import sys,json
d=json.load(sys.stdin); items=d.get('data',[]) if isinstance(d,dict) else d
print(next((t['id'] for t in items if t['name']=='$name'),''))")
  if [ -n "$id" ]; then
    echo "  = trust domain '$name' exists ($id)" >&2
  else
    am -X POST "$base" -d "{\"name\":\"$name\",\"description\":\"SPIRE issuer ($name)\",\"bundleSource\":\"JWKS_URL\",\"jwksUrl\":\"$jwks\"}" >/dev/null
    echo "  + registered trust domain '$name' -> $jwks" >&2
  fi
}

# lookup-or-create an organization service account by username. echoes user id.
am_service_account() { # <username> <password>
  local user=$1 pass=$2 id
  id=$(am "$AM_ORG/users?size=100" | py "import sys,json
d=json.load(sys.stdin); items=d.get('data',[]) if isinstance(d,dict) else d
print(next((u['id'] for u in items if u.get('username')=='$user'),''))")
  if [ -n "$id" ]; then
    echo "  = service account '$user' exists ($id)" >&2
  else
    id=$(am -X POST "$AM_ORG/users" \
      -d "{\"username\":\"$user\",\"password\":\"$pass\",\"serviceAccount\":true,\"enabled\":true}" \
      | py 'import sys,json;print(json.load(sys.stdin)["id"])')
    echo "  + created service account '$user' ($id)" >&2
  fi
  echo "$id"
}

# ── APIM auth (basic works directly on the v2 API) ────────────────────────────
apim() { curl -fsS -u "$APIM_CREDS" -H 'Content-Type: application/json' "$@"; }

# ── RECIPE ────────────────────────────────────────────────────────────────────
# Stand up the `gamma` domain with every agent-identity feature enabled so the
# demo works out of the box: open DCR, a CIMD default-template app bound to the
# default IdP, SPIFFE (validated against the SPIRE issuer wired into compose),
# and CIBA. SPIRE serves its JWKS at http://spire-oidc:8443/keys on this network.
echo "── AM: gamma domain ──"
GAMMA_ID=$(am_domain "gamma" "Gamma demo domain")

# Open Dynamic Client Registration (RFC 7591) + relax redirect rules so MCP
# clients can self-register on loopback/http redirect URIs.
am -X PATCH "$AM_DOMAINS/$GAMMA_ID" -d '{"oidc":{"clientRegistrationSettings":{
  "isDynamicClientRegistrationEnabled":true,
  "isOpenDynamicClientRegistrationEnabled":true,
  "allowLocalhostRedirectUri":true,
  "allowHttpSchemeRedirectUri":true}}}' >/dev/null
echo "  ✓ open DCR + loopback/http redirects"

# Default-template app: CIMD-derived clients inherit this config + the default IdP.
IDP_ID=$(am_default_idp "$GAMMA_ID")
TEMPLATE_ID=$(am_app "$GAMMA_ID" "default-template" "WEB" '["http://localhost/callback","http://127.0.0.1/callback"]')
am -X PATCH "$AM_DOMAINS/$GAMMA_ID/applications/$TEMPLATE_ID" -d "{
  \"template\":true,
  \"identityProviders\":[{\"identity\":\"$IDP_ID\",\"priority\":0}],
  \"settings\":{\"oauth\":{
    \"grantTypes\":[\"authorization_code\",\"refresh_token\",\"urn:ietf:params:oauth:grant-type:token-exchange\"],
    \"scopeSettings\":[
      {\"scope\":\"openid\",\"defaultScope\":true},
      {\"scope\":\"profile\",\"defaultScope\":false},
      {\"scope\":\"email\",\"defaultScope\":false},
      {\"scope\":\"offline_access\",\"defaultScope\":false}]}}}" >/dev/null
echo "  ✓ default-template app ($TEMPLATE_ID) — web + token-exchange + default IdP"

# Enable CIMD (pointing at the template), SPIFFE workload identity, and CIBA.
# allowPrivateIpAddress/allowUnsecuredHttpUri let AM reach the in-network SPIRE
# JWKS (http://spire-oidc:8443) and private CIMD metadata hosts.
am -X PATCH "$AM_DOMAINS/$GAMMA_ID" -d "{\"oidc\":{
  \"cimdSettings\":{\"enabled\":true,\"templateId\":\"$TEMPLATE_ID\",\"allowPrivateIpAddress\":true,\"allowUnsecuredHttpUri\":true},
  \"workloadIdentitySettings\":{\"enabled\":true,\"allowPrivateIpAddress\":true,\"allowUnsecuredHttpUri\":true},
  \"cibaSettings\":{\"enabled\":true}}}" >/dev/null
echo "  ✓ CIMD + SPIFFE + CIBA enabled"

# Register the SPIRE trust domain so JWT-SVIDs (spiffe://am.local/...) validate.
am_trust_domain "$GAMMA_ID" "am.local" "http://spire-oidc:8443/keys"

echo "── APIM: reachable ──"
apim "$APIM_URL/management/v2/environments/$ENV/apis?page=1&perPage=1" >/dev/null \
  && echo "  ok (admin can reach the APIM v2 mgmt API)"

# ── Portal Next ──────────────────────────────────────────────────────────────
# The compose sets DEFAULT_PORTAL=next on the portal + console containers so
# Portal Next is served at /, but the backend feature gate (portalNext.access.
# enabled) defaults to false — enable it so Portal Next doesn't redirect to /404.
# Also set portal.url so the console's "Open Website" link points at the portal.
# Both are sent in a single POST to avoid one overwriting the other (the settings
# API replaces top-level keys, it doesn't deep-merge).
echo "── APIM: Portal Next ──"
APIM_SETTINGS="$APIM_URL/management/organizations/$ORG/environments/$ENV/settings"
PORTAL_URL="http://portal.localhost"

CURRENT=$(apim "$APIM_SETTINGS")
PORTAL_NEXT_ENABLED=$(echo "$CURRENT" | py 'import sys,json;d=json.load(sys.stdin);print(str(d.get("portalNext",{}).get("access",{}).get("enabled",False)).lower())' 2>/dev/null || echo "false")
PORTAL_URL_CURRENT=$(echo "$CURRENT" | py 'import sys,json;d=json.load(sys.stdin);print(d.get("portal",{}).get("url",""))' 2>/dev/null || echo "")

if [ "$PORTAL_NEXT_ENABLED" = "true" ] && [ "$PORTAL_URL_CURRENT" = "$PORTAL_URL" ]; then
  echo "  = Portal Next enabled, portal.url correct"
else
  apim -X POST "$APIM_SETTINGS" \
    -d "{\"portalNext\":{\"access\":{\"enabled\":true}},\"portal\":{\"url\":\"$PORTAL_URL\"}}" >/dev/null
  [ "$PORTAL_NEXT_ENABLED" != "true" ] && echo "  ✓ Portal Next access enabled"
  [ "$PORTAL_URL_CURRENT" != "$PORTAL_URL" ] && echo "  ✓ portal.url set to $PORTAL_URL"
fi

# ── Gamma AIM ↔ AM connection ─────────────────────────────────────────────────
# Provision a dedicated AM service account (ORGANIZATION_OWNER), mint a token, and
# save it as the AIM module's per-org AM connection so the agent UI works without a
# manual settings save. The module (in the rest-api container) reaches AM by its
# in-network name, so we store http://management:8093 — not localhost.
echo "── Gamma: AIM ↔ AM connection ──"
AIM_CFG="$APIM_URL/gamma/organizations/$ORG/modules/aim/identity/am-config"
# Idempotency: skip the whole block if a token is already stored (hasAccessToken).
HAS_TOKEN=$(curl -fsS -u "$APIM_CREDS" "$AIM_CFG" 2>/dev/null \
  | py 'import sys,json
try: print(str(json.load(sys.stdin).get("hasAccessToken", False)).lower())
except Exception: print("false")' || echo false)

if [ "$HAS_TOKEN" = "true" ]; then
  echo "  = AM connection already saved in Gamma (skipping service-account provisioning)"
else
  SA_ID=$(am_service_account "gamma-aim-sa" "GammaAim123!")
  ROLE_ID=$(am "$AM_ORG/roles?size=100" | py "import sys,json
d=json.load(sys.stdin); items=d.get('data',[]) if isinstance(d,dict) else d
print(next((r['id'] for r in items if r.get('name')=='ORGANIZATION_OWNER'),''))")
  am -X POST "$AM_ORG/members" \
    -d "{\"memberId\":\"$SA_ID\",\"memberType\":\"USER\",\"role\":\"$ROLE_ID\"}" >/dev/null
  echo "  ✓ service account gamma-aim-sa granted ORGANIZATION_OWNER"

  SA_TOKEN=$(am -X POST "$AM_ORG/users/$SA_ID/tokens" -d '{"name":"gamma-aim"}' \
    | py 'import sys,json;print(json.load(sys.stdin)["token"])')

  curl -fsS -u "$APIM_CREDS" -H 'Content-Type: application/json' -X PUT "$AIM_CFG" \
    -d "{\"baseUrl\":\"http://management:8093\",\"serviceAccountAccessToken\":\"$SA_TOKEN\",\"gatewayUrl\":\"http://gateway:8092\",\"defaultDomainId\":\"$GAMMA_ID\",\"defaultDomainHrid\":\"gamma\"}" \
    >/dev/null
  echo "  ✓ AM connection saved in Gamma (baseUrl=http://management:8093, domain=gamma)"
fi

echo "✓ environment prepared"
