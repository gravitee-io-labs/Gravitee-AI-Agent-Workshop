#!/usr/bin/env python3
"""
Gravitee Access Management (AM) Initialization Script.
Configures a security domain, applications (from YAML), users, and MCP servers.
"""

import json
import os
import re
import sys
import time
import traceback
from glob import glob
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AM_BASE_URL = os.getenv("AM_BASE_URL", "http://localhost:8093")
AM_USERNAME = os.getenv("AM_USERNAME", "admin")
AM_PASSWORD = os.getenv("AM_PASSWORD", "adminadmin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")
DOMAIN_NAME = "gravitee"

APPS_CONFIG_DIR = os.getenv("APPS_CONFIG_DIR", "/app/am-apps")
MCP_SERVERS_CONFIG_DIR = os.getenv("MCP_SERVERS_CONFIG_DIR", "/app/am-mcp-servers")

# Gamma <-> AM linking configuration
GAMMA_SERVICE_ACCOUNT_USERNAME = os.getenv("GAMMA_SERVICE_ACCOUNT_USERNAME", "Gamma")
GAMMA_SERVICE_ACCOUNT_TOKEN_NAME = os.getenv("GAMMA_SERVICE_ACCOUNT_TOKEN_NAME", "Gamma")
GAMMA_AM_BASE_URL = os.getenv("GAMMA_AM_BASE_URL", "http://host.docker.internal:8093")
GAMMA_AM_CONFIG_FILE = os.getenv("GAMMA_AM_CONFIG_FILE", "/tmp/gamma-am-config.json")

# User configuration
USER_PASSWORD = "HelloWorld@123"
USERS = [
    {"firstName": "Customer", "lastName": "Demo",  "email": "customer.demo@gravitee.io",  "username": "customer.demo@gravitee.io"},
    {"firstName": "Hotel",    "lastName": "Admin", "email": "hotel.admin@gravitee.io",    "username": "hotel.admin@gravitee.io"},
]

MAX_RETRIES = 30
RETRY_DELAY = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml_configs(directory: str, log_fn) -> List[Dict[str, Any]]:
    """Load all YAML configs from *directory* that have a 'name' key."""
    log_fn(f"Loading configurations from {directory}...")
    configs: List[Dict[str, Any]] = []
    yaml_files = sorted(glob(os.path.join(directory, "*.yaml")) + glob(os.path.join(directory, "*.yml")))

    if not yaml_files:
        log_fn(f"  WARNING: No YAML files found in {directory}")
        return configs

    for path in yaml_files:
        try:
            with open(path, "r") as fh:
                cfg = yaml.safe_load(fh)
                if cfg and cfg.get("name"):
                    configs.append(cfg)
                    log_fn(f"  Loaded: {cfg['name']} ({os.path.basename(path)})")
        except Exception as exc:
            log_fn(f"  WARNING: Failed to load {path}: {exc}")

    log_fn(f"✓ Loaded {len(configs)} configuration(s)")
    return configs


# ───────────────────────────────────────────────────────────────────────────
# Gravitee AM Initializer
# ───────────────────────────────────────────────────────────────────────────

class GraviteeInitializer:
    """Handles Gravitee Access Management initialization."""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.domain_id: Optional[str] = None
        self.apps: List[Dict[str, Any]] = []
        self.session = requests.Session()
        self.gamma_service_account: Optional[Dict[str, str]] = None

    # -- Logging & error helpers -------------------------------------------

    def log(self, message: str):
        print(f"[GRAVITEE-INIT] {message}", flush=True)

    def _log_response_error(self, label: str, exc: requests.exceptions.RequestException):
        self.log(f"ERROR: {label}: {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "text"):
            self.log(f"  Response: {resp.text}")

    @staticmethod
    def _mask_token(token: str) -> str:
        if not token:
            return ""
        if len(token) <= 12:
            return "***"
        return f"{token[:6]}...{token[-6:]}"

    # -- URL helpers -------------------------------------------------------

    @property
    def _domain_url(self) -> str:
        return (
            f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}"
            f"/environments/{ENVIRONMENT}/domains/{self.domain_id}"
        )

    def _app_url(self, app_id: str) -> str:
        return f"{self._domain_url}/applications/{app_id}"

    # -- Readiness & auth --------------------------------------------------

    def wait_for_am_api(self) -> bool:
        self.log("Waiting for Access Management API to be ready...")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.session.get(
                    f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}",
                    timeout=5,
                )
                if r.status_code in (200, 401):
                    self.log("Access Management API is ready!")
                    return True
            except requests.exceptions.RequestException as exc:
                self.log(f"  Attempt {attempt}/{MAX_RETRIES}: not ready yet ({exc})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        self.log("ERROR: Access Management API did not become ready in time")
        return False

    def authenticate(self) -> bool:
        self.log("Authenticating with Access Management...")
        try:
            r = self.session.post(
                f"{AM_BASE_URL}/management/auth/token",
                auth=(AM_USERNAME, AM_PASSWORD),
                timeout=10,
            )
            r.raise_for_status()
            self.access_token = r.json().get("access_token")
            if not self.access_token:
                self.log("ERROR: No access token in response")
                return False
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            })
            self.log("✓ Successfully authenticated")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Authentication failed", exc)
            return False

    # -- Domain management -------------------------------------------------

    def create_domain(self) -> bool:
        self.log(f"Creating security domain '{DOMAIN_NAME}'...")
        url = (
            f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}"
            f"/environments/{ENVIRONMENT}/domains"
        )
        try:
            r = self.session.post(url, json={
                "name": DOMAIN_NAME,
                "description": "Security domain for Gravitee Hotels application",
                "dataPlaneId": "default",
            }, timeout=10)

            if r.status_code == 400 and "already exists" in r.text.lower():
                self.log(f"Domain '{DOMAIN_NAME}' already exists, fetching it...")
                return self._get_existing_domain()

            r.raise_for_status()
            self.domain_id = r.json().get("id")
            if not self.domain_id:
                self.log("ERROR: No domain ID in response")
                return False
            self.log(f"✓ Domain created with ID: {self.domain_id}")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create domain", exc)
            return False

    def _get_existing_domain(self) -> bool:
        url = (
            f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}"
            f"/environments/{ENVIRONMENT}/domains"
        )
        try:
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            domains = r.json()
            if isinstance(domains, dict) and "data" in domains:
                domains = domains["data"]
            for d in domains:
                if d.get("name") == DOMAIN_NAME:
                    self.domain_id = d["id"]
                    enabled = d.get("enabled", False)
                    self.log(f"✓ Found existing domain with ID: {self.domain_id} (enabled: {enabled})")
                    return True
            self.log(f"ERROR: Domain '{DOMAIN_NAME}' not found")
            return False
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to get domains", exc)
            return False

    def configure_domain(self) -> bool:
        """Enable domain, configure DCR and Token Exchange in a single PATCH."""
        self.log("Configuring domain (enable + DCR + Token Exchange)...")
        try:
            r = self.session.patch(self._domain_url, json={
                "enabled": True,
                "oidc": {
                    "clientRegistrationSettings": {
                        "allowLocalhostRedirectUri": True,
                        "allowHttpSchemeRedirectUri": True,
                    }
                },
                "tokenExchangeSettings": {
                    "enabled": True,
                    "allowedSubjectTokenTypes": [
                        "urn:ietf:params:oauth:token-type:access_token",
                        "urn:ietf:params:oauth:token-type:refresh_token",
                        "urn:ietf:params:oauth:token-type:id_token",
                        "urn:ietf:params:oauth:token-type:jwt",
                    ],
                    "allowedRequestedTokenTypes": [
                        "urn:ietf:params:oauth:token-type:access_token",
                        "urn:ietf:params:oauth:token-type:id_token",
                    ],
                    "allowImpersonation": False,
                    "allowedActorTokenTypes": [
                        "urn:ietf:params:oauth:token-type:access_token",
                        "urn:ietf:params:oauth:token-type:id_token",
                        "urn:ietf:params:oauth:token-type:jwt",
                    ],
                    "allowDelegation": True,
                    "trustedIssuers": [],
                    "maxDelegationDepth": 25,
                    "tokenExchangeOAuthSettings": {
                        "scopeHandling": "downscoping",
                        "inherited": False,
                    },
                },
            }, timeout=10)
            r.raise_for_status()
            self.log("✓ Domain configured (enabled + DCR + Token Exchange)")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to configure domain", exc)
            return False

    # -- Application management --------------------------------------------

    def _build_oauth_payload(self, app_config: Dict[str, Any]) -> Dict[str, Any]:
        """Translate user-friendly YAML 'oauth' section into AM API payload."""
        oauth = app_config.get("oauth", {})
        if not oauth:
            return {}

        scopes = app_config.get("scopes", [])
        redirect_uris = app_config.get("redirectUris", [])

        settings: Dict[str, Any] = {
            "grantTypes": oauth.get("grantTypes", []),
            "responseTypes": ["code", "code id_token token", "code id_token", "code token"],
            "redirectUris": redirect_uris,
            "tokenEndpointAuthMethod": oauth.get("tokenEndpointAuthMethod", "client_secret_basic"),
            "disableRefreshTokenRotation": False,
            "enhanceScopesWithUserPermissions": False,
        }

        # PKCE
        pkce = oauth.get("pkce", False)
        settings["forcePKCE"] = pkce
        settings["forceS256CodeChallengeMethod"] = pkce

        # Token validity
        validity = oauth.get("tokenValidity", {})
        settings["accessTokenValiditySeconds"] = validity.get("accessToken", 7200)
        settings["refreshTokenValiditySeconds"] = validity.get("refreshToken", 14400)
        settings["idTokenValiditySeconds"] = validity.get("idToken", 14400)

        # Token Exchange
        tx = oauth.get("tokenExchange")
        if tx:
            settings["tokenExchangeOAuthSettings"] = {
                "inherited": tx.get("inherited", True),
                "scopeHandling": tx.get("scopeHandling", "downscoping"),
            }

        # Scopes → scopeSettings
        if scopes:
            settings["scopeSettings"] = [
                {"scope": s, "defaultScope": False, "scopeApproval": 300}
                for s in scopes
            ]

        # Token custom claims
        claims = oauth.get("tokenCustomClaims", [])
        settings["tokenCustomClaims"] = [
            {"tokenType": c.get("tokenType", "access_token"), "claimName": c["claimName"], "claimValue": c["claimValue"]}
            for c in claims
        ] if claims else []

        return {"settings": {"oauth": settings}}

    def create_application(self, app_config: Dict[str, Any]) -> Optional[str]:
        app_name = app_config["name"]
        client_id = app_config["clientId"]
        app_type = app_config.get("type", "BROWSER")

        self.log(f"Creating application '{app_name}' (type: {app_type})...")

        payload: Dict[str, Any] = {
            "name": app_name,
            "type": app_type,
            "clientId": client_id,
            "clientSecret": app_config.get("clientSecret"),
            "redirectUris": app_config.get("redirectUris", []),
        }
        if app_config.get("description"):
            payload["description"] = app_config["description"]
        if app_config.get("agentCardUrl"):
            payload["agentCardUrl"] = app_config["agentCardUrl"]

        try:
            r = self.session.post(f"{self._domain_url}/applications", json=payload, timeout=10)
            if r.status_code == 400 and ("already exists" in r.text.lower() or "clientid" in r.text.lower()):
                self.log(f"  Application with client ID '{client_id}' already exists, fetching it...")
                return self._get_existing_application(client_id)
            r.raise_for_status()
            app_id = r.json().get("id")
            if not app_id:
                self.log("ERROR: No application ID in response")
                return None
            self.log(f"✓ Application created with ID: {app_id}")
            return app_id
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create application", exc)
            return None

    def _get_existing_application(self, client_id: str) -> Optional[str]:
        """Find application by OAuth clientId."""
        try:
            r = self.session.get(
                f"{self._domain_url}/applications",
                params={"q": client_id},
                timeout=10,
            )
            r.raise_for_status()
            apps = r.json()
            if isinstance(apps, dict) and "data" in apps:
                apps = apps["data"]

            for app in apps:
                app_id = app.get("id")
                if not app_id:
                    continue
                try:
                    detail = self.session.get(self._app_url(app_id), timeout=10)
                    detail.raise_for_status()
                    if detail.json().get("settings", {}).get("oauth", {}).get("clientId") == client_id:
                        self.log(f"✓ Found existing application with ID: {app_id}")
                        return app_id
                except requests.exceptions.RequestException:
                    continue

            self.log(f"ERROR: Application with client ID '{client_id}' not found")
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to search applications", exc)
            return None

    def configure_application_settings(self, app_id: str, app_config: Dict[str, Any]) -> bool:
        app_name = app_config["name"]
        self.log(f"Configuring OAuth settings for '{app_name}'...")

        payload = self._build_oauth_payload(app_config)
        if not payload:
            self.log(f"  No OAuth settings to configure for '{app_name}'")
            return True

        try:
            r = self.session.patch(self._app_url(app_id), json=payload, timeout=10)
            r.raise_for_status()
            grants = r.json().get("settings", {}).get("oauth", {}).get("grantTypes", [])
            self.log(f"  ✓ OAuth settings configured — grantTypes: {grants}")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to configure settings", exc)
            return False

    def _add_identity_provider(self, app_id: str, app_name: str) -> bool:
        self.log(f"Adding default identity provider to '{app_name}'...")
        try:
            r = self.session.get(f"{self._domain_url}/identities", timeout=10)
            r.raise_for_status()
            idps = r.json()
            system_idp = next((idp for idp in idps if idp.get("system")), None)
            if not system_idp:
                self.log("ERROR: No system identity provider found")
                return False
            self.log(f"  Found system identity provider: {system_idp.get('name', 'Unknown')}")

            r2 = self.session.patch(self._app_url(app_id), json={
                "identityProviders": [{
                    "identity": system_idp["id"],
                    "selectionRule": "",
                    "priority": 0,
                }]
            }, timeout=10)
            r2.raise_for_status()
            self.log(f"  ✓ Identity provider added to '{app_name}'")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to add identity provider", exc)
            return False

    def _create_all_applications(self, app_configs: List[Dict[str, Any]]) -> bool:
        if not app_configs:
            self.log("WARNING: No application configurations to process")
            return True

        for cfg in app_configs:
            name = cfg["name"]
            app_id = self.create_application(cfg)
            if not app_id:
                return False
            if not self.configure_application_settings(app_id, cfg):
                return False
            if not self._add_identity_provider(app_id, name):
                return False

            self.apps.append({
                "name": name,
                "id": app_id,
                "clientId": cfg["clientId"],
                "clientSecret": cfg.get("clientSecret"),
                "type": cfg.get("type", "BROWSER"),
            })
            self.log(f"✓ Application '{name}' fully configured")
        return True

    # -- User management ---------------------------------------------------

    def create_user(self) -> bool:
        for user in USERS:
            username = user["username"]
            self.log(f"Creating user '{username}'...")
            try:
                r = self.session.post(f"{self._domain_url}/users", json={
                    "firstName": user["firstName"],
                    "lastName": user["lastName"],
                    "email": user["email"],
                    "username": username,
                    "password": USER_PASSWORD,
                    "forceResetPassword": False,
                    "preRegistration": False,
                }, timeout=10)
                if r.status_code == 400 and "already exists" in r.text.lower():
                    self.log(f"✓ User '{username}' already exists, skipping creation")
                    continue
                r.raise_for_status()
                self.log(f"✓ User '{username}' created successfully")
            except requests.exceptions.RequestException as exc:
                self._log_response_error(f"Failed to create user '{username}'", exc)
                return False
        return True

    # -- MCP Servers -------------------------------------------------------

    def _create_mcp_server(self, cfg: Dict[str, Any]) -> Optional[str]:
        name = cfg["name"]
        client_id = cfg["clientId"]
        self.log(f"Creating MCP Server '{name}'...")

        features = [
            {"key": t["key"], "description": t.get("description", ""), "type": t.get("type", "MCP_TOOL"), "scopes": t.get("scopes", [])}
            for t in cfg.get("tools", [])
        ]
        payload = {
            "name": name,
            "description": cfg.get("description", ""),
            "resourceIdentifiers": cfg.get("resourceIdentifiers", []),
            "clientId": client_id,
            "clientSecret": cfg.get("clientSecret"),
            "type": cfg.get("type", "MCP_SERVER"),
            "features": features,
        }

        try:
            r = self.session.post(f"{self._domain_url}/protected-resources", json=payload, timeout=10)
            if r.status_code == 400 and ("already exists" in r.text.lower() or "clientid" in r.text.lower()):
                self.log(f"  MCP Server with client ID '{client_id}' may already exist, checking...")
                return self._get_existing_mcp_server(client_id)
            r.raise_for_status()
            rid = r.json().get("id")
            if not rid:
                self.log("ERROR: No protected resource ID in response")
                return None
            self.log(f"✓ MCP Server '{name}' created with ID: {rid}")
            return rid
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create MCP Server", exc)
            return None

    def _get_existing_mcp_server(self, client_id: str) -> Optional[str]:
        try:
            r = self.session.get(
                f"{self._domain_url}/protected-resources",
                params={"type": "MCP_SERVER"},
                timeout=10,
            )
            r.raise_for_status()
            for res in r.json().get("data", []):
                if res.get("clientId") == client_id:
                    self.log(f"✓ Found existing MCP Server with ID: {res['id']}")
                    return res["id"]
            self.log(f"MCP Server with client ID '{client_id}' not found")
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to search MCP Servers", exc)
            return None

    def _create_all_mcp_servers(self, mcp_configs: List[Dict[str, Any]]) -> bool:
        if not mcp_configs:
            self.log("No MCP server configurations to process")
            return True
        for cfg in mcp_configs:
            rid = self._create_mcp_server(cfg)
            if not rid:
                return False
            self.log(f"✓ MCP Server '{cfg['name']}' configured with {len(cfg.get('tools', []))} tool(s)")
        return True

    # -- Gamma AM service account ----------------------------------------

    def _get_roles(self) -> List[Dict[str, Any]]:
        try:
            r = self.session.get(
                f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/roles",
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("data", [])
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to load AM roles", exc)
            return []

    def _find_role_id(self, role_name: str) -> Optional[str]:
        for role in self._get_roles():
            if role.get("name") == role_name:
                return role.get("id")
        self.log(f"ERROR: Role '{role_name}' not found")
        return None

    def _find_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.session.get(
                f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/users",
                params={"q": username},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            for user in data:
                if (user.get("username") or "").lower() == username.lower():
                    return user
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to search users", exc)
            return None

    def _ensure_gamma_service_account_user(self) -> Optional[str]:
        self.log(f"Ensuring Gamma service account '{GAMMA_SERVICE_ACCOUNT_USERNAME}'...")
        existing = self._find_user_by_username(GAMMA_SERVICE_ACCOUNT_USERNAME)
        if existing:
            uid = existing.get("id")
            self.log(f"✓ Gamma service account already exists (ID: {uid})")
            return uid

        try:
            r = self.session.post(
                f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/users",
                json={"username": GAMMA_SERVICE_ACCOUNT_USERNAME, "serviceAccount": True},
                timeout=10,
            )
            r.raise_for_status()
            uid = r.json().get("id")
            if not uid:
                self.log("ERROR: Gamma service account created without ID")
                return None
            self.log(f"✓ Gamma service account created (ID: {uid})")
            return uid
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create Gamma service account", exc)
            return None

    def _member_has_role(self, members_payload: Dict[str, Any], member_id: str, role_id: str) -> bool:
        for membership in members_payload.get("memberships", []):
            if membership.get("memberId") == member_id and membership.get("roleId") == role_id:
                return True
        return False

    def _ensure_org_member_role(self, member_id: str, role_id: str) -> bool:
        try:
            members_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/members"
            r = self.session.get(members_url, timeout=10)
            r.raise_for_status()
            payload = r.json()
            if self._member_has_role(payload, member_id, role_id):
                self.log("✓ Gamma service account already has ORGANIZATION_OWNER")
                return True

            r2 = self.session.post(
                members_url,
                json={"memberId": member_id, "memberType": "USER", "role": role_id},
                timeout=10,
            )
            r2.raise_for_status()
            self.log("✓ Added Gamma service account as ORGANIZATION_OWNER")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to add organization membership", exc)
            return False

    def _ensure_domain_member_role(self, member_id: str, role_id: str) -> bool:
        try:
            members_url = f"{self._domain_url}/members"
            r = self.session.get(members_url, timeout=10)
            r.raise_for_status()
            payload = r.json()
            if self._member_has_role(payload, member_id, role_id):
                self.log("✓ Gamma service account already has DOMAIN_OWNER")
                return True

            r2 = self.session.post(
                members_url,
                json={"memberId": member_id, "memberType": "USER", "role": role_id},
                timeout=10,
            )
            r2.raise_for_status()
            self.log("✓ Added Gamma service account as DOMAIN_OWNER")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to add domain membership", exc)
            return False

    def _create_service_account_token(self, user_id: str) -> Optional[str]:
        self.log(f"Creating AM token '{GAMMA_SERVICE_ACCOUNT_TOKEN_NAME}' for Gamma service account...")
        try:
            r = self.session.post(
                f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/users/{user_id}/tokens",
                json={"name": GAMMA_SERVICE_ACCOUNT_TOKEN_NAME},
                timeout=10,
            )
            r.raise_for_status()
            token = r.json().get("token")
            if not token:
                self.log("ERROR: Service account token creation returned no token")
                return None
            self.log(f"✓ Service account token generated ({self._mask_token(token)})")
            return token
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create service account token", exc)
            return None

    def _write_gamma_am_config(self, service_account_token: str) -> bool:
        self.log(f"Writing Gamma AM link config to {GAMMA_AM_CONFIG_FILE}...")
        payload = {
            "baseUrl": GAMMA_AM_BASE_URL,
            "serviceAccountAccessToken": service_account_token,
            "amOrganizationId": ORGANIZATION,
            "environmentId": None,
            "defaultDomainId": self.domain_id,
            "defaultDomainHrid": DOMAIN_NAME,
            "gatewayUrl": None,
        }
        try:
            target = Path(GAMMA_AM_CONFIG_FILE)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload), encoding="utf-8")
            self.log("✓ Gamma AM link config written")
            return True
        except Exception as exc:
            self.log(f"ERROR: Failed to write Gamma AM link config: {exc}")
            return False

    def setup_gamma_service_account(self) -> bool:
        self.log("Configuring AM service account for Gamma...")
        org_owner_role = self._find_role_id("ORGANIZATION_OWNER")
        domain_owner_role = self._find_role_id("DOMAIN_OWNER")
        if not org_owner_role or not domain_owner_role:
            return False

        user_id = self._ensure_gamma_service_account_user()
        if not user_id:
            return False

        if not self._ensure_org_member_role(user_id, org_owner_role):
            return False
        if not self._ensure_domain_member_role(user_id, domain_owner_role):
            return False

        token = self._create_service_account_token(user_id)
        if not token:
            return False

        self.gamma_service_account = {
            "id": user_id,
            "username": GAMMA_SERVICE_ACCOUNT_USERNAME,
            "tokenMask": self._mask_token(token),
        }
        return self._write_gamma_am_config(token)

    # -- Orchestration -----------------------------------------------------

    def run(self) -> bool:
        self.log("Starting Gravitee Access Management initialization...")
        self.log("=" * 80)

        if not self.wait_for_am_api():
            return False
        if not self.authenticate():
            return False
        if not self.create_domain():
            return False
        if not self.configure_domain():
            return False

        app_configs = _load_yaml_configs(APPS_CONFIG_DIR, self.log)
        if not self._create_all_applications(app_configs):
            return False
        if not self.create_user():
            return False

        mcp_configs = _load_yaml_configs(MCP_SERVERS_CONFIG_DIR, self.log)
        if not self._create_all_mcp_servers(mcp_configs):
            return False

        if not self.setup_gamma_service_account():
            return False

        self.log("=" * 80)
        self.log("✓ Access Management initialization completed successfully!")
        self.log("")
        self.log("Summary:")
        self.log(f"  - Domain: {DOMAIN_NAME} (ID: {self.domain_id})")
        self.log(f"  - Applications created: {len(self.apps)}")
        for app in self.apps:
            self.log(f"    • {app['name']} ({app['type']})")
            self.log(f"      Client ID: {app['clientId']}")
        for u in USERS:
            self.log(f"  - User: {u['username']}")
        self.log(f"  - MCP Servers created: {len(mcp_configs)}")
        for mcp in mcp_configs:
            self.log(f"    • {mcp['name']}")
            self.log(f"      Client ID: {mcp['clientId']}")
            self.log(f"      Tools: {[t['key'] for t in mcp.get('tools', [])]}")
        if self.gamma_service_account:
            self.log("  - Gamma AM Service Account:")
            self.log(f"    • Username: {self.gamma_service_account['username']}")
            self.log(f"      ID: {self.gamma_service_account['id']}")
            self.log(f"      Token: {self.gamma_service_account['tokenMask']}")
            self.log(f"      Shared config: {GAMMA_AM_CONFIG_FILE}")
        return True


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

def main():
    am = GraviteeInitializer()

    try:
        if not am.run():
            sys.exit(1)
    except KeyboardInterrupt:
        am.log("Initialization interrupted by user"); sys.exit(1)
    except Exception as exc:
        am.log(f"FATAL ERROR: {exc}"); traceback.print_exc(); sys.exit(1)

    am.log("✓ Access Management initialization completed")
    print("[INIT] ✓ All initialization completed successfully!", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
