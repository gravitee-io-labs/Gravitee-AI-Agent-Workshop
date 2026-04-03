#!/usr/bin/env python3
"""
Gravitee Access Management (AM) Initialization Script.
Configures a security domain, applications (from YAML), users, MCP servers,
Token Exchange (RFC 8693), and OpenFGA authorization.
"""

import json
import os
import re
import sys
import time
import traceback
from glob import glob
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

# User configuration
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"
USER_EMAIL = "john.doe@gravitee.io"
USER_USERNAME = "john.doe@gravitee.io"
USER_PASSWORD = "HelloWorld@123"

MAX_RETRIES = 30
RETRY_DELAY = 5

# OpenFGA
FGA_BASE_URL = os.getenv("FGA_BASE_URL", "http://openfga:8080")
FGA_STORE_NAME = "Hotel Booking Authorization"
FGA_CONFIG_FILE = os.getenv("FGA_CONFIG_FILE", "/app/openfga/openfgastore.yaml")
OPENFGA_SERVER_URL = os.getenv("OPENFGA_SERVER_URL", "http://openfga:8080")


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

    # -- Logging & error helpers -------------------------------------------

    def log(self, message: str):
        print(f"[GRAVITEE-INIT] {message}", flush=True)

    def _log_response_error(self, label: str, exc: requests.exceptions.RequestException):
        self.log(f"ERROR: {label}: {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "text"):
            self.log(f"  Response: {resp.text}")

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
        self.log(f"Creating user '{USER_USERNAME}'...")
        try:
            r = self.session.post(f"{self._domain_url}/users", json={
                "firstName": USER_FIRST_NAME,
                "lastName": USER_LAST_NAME,
                "email": USER_EMAIL,
                "username": USER_USERNAME,
                "password": USER_PASSWORD,
                "forceResetPassword": False,
                "preRegistration": False,
            }, timeout=10)
            if r.status_code == 400 and "already exists" in r.text.lower():
                self.log(f"✓ User '{USER_USERNAME}' already exists, skipping creation")
                return True
            r.raise_for_status()
            self.log(f"✓ User '{USER_USERNAME}' created successfully")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create user", exc)
            return False

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

    # -- OpenFGA authorization engine --------------------------------------

    def create_openfga_authorization_engine(self, store_id: str, authorization_model_id: str = None) -> bool:
        self.log("Creating OpenFGA authorization engine...")
        url = f"{self._domain_url}/authorization-engines"

        try:
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            for engine in r.json():
                if engine.get("type") == "openfga":
                    self.log(f"✓ OpenFGA authorization engine already exists with ID: {engine['id']}")
                    return True
        except requests.exceptions.RequestException:
            pass  # will try to create

        configuration = {"connectionUri": OPENFGA_SERVER_URL, "storeId": store_id}
        if authorization_model_id:
            configuration["authorizationModelId"] = authorization_model_id

        try:
            r = self.session.post(url, json={
                "type": "openfga",
                "name": "OpenFGA Authorization Engine",
                "configuration": json.dumps(configuration),
            }, timeout=10)
            if r.status_code == 400 and "already exists" in r.text.lower():
                self.log("✓ OpenFGA authorization engine already exists")
                return True
            r.raise_for_status()
            self.log(f"✓ OpenFGA authorization engine created with ID: {r.json().get('id')}")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create OpenFGA authorization engine", exc)
            return False

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

        self.log("=" * 80)
        self.log("✓ Access Management initialization completed successfully!")
        self.log("")
        self.log("Summary:")
        self.log(f"  - Domain: {DOMAIN_NAME} (ID: {self.domain_id})")
        self.log(f"  - Applications created: {len(self.apps)}")
        for app in self.apps:
            self.log(f"    • {app['name']} ({app['type']})")
            self.log(f"      Client ID: {app['clientId']}")
        self.log(f"  - User: {USER_USERNAME}")
        self.log(f"  - MCP Servers created: {len(mcp_configs)}")
        for mcp in mcp_configs:
            self.log(f"    • {mcp['name']}")
            self.log(f"      Client ID: {mcp['clientId']}")
            self.log(f"      Tools: {[t['key'] for t in mcp.get('tools', [])]}")
        return True


# ───────────────────────────────────────────────────────────────────────────
# OpenFGA Initializer
# ───────────────────────────────────────────────────────────────────────────

class OpenFGAInitializer:
    """Handles OpenFGA authorization store initialization."""

    def __init__(self):
        self.store_id: Optional[str] = None
        self.authorization_model_id: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def log(self, message: str):
        print(f"[OPENFGA-INIT] {message}", flush=True)

    def _log_response_error(self, label: str, exc: requests.exceptions.RequestException):
        self.log(f"ERROR: {label}: {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "text"):
            self.log(f"  Response: {resp.text}")

    # -- Readiness ---------------------------------------------------------

    def wait_for_fga_api(self) -> bool:
        self.log("Waiting for OpenFGA API to be ready...")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if self.session.get(f"{FGA_BASE_URL}/stores", timeout=5).status_code == 200:
                    self.log("OpenFGA API is ready!")
                    return True
            except requests.exceptions.RequestException as exc:
                self.log(f"  Attempt {attempt}/{MAX_RETRIES}: not ready yet ({exc})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        self.log("ERROR: OpenFGA API did not become ready in time")
        return False

    # -- Store management --------------------------------------------------

    def get_or_create_store(self) -> bool:
        self.log(f"Creating/finding store '{FGA_STORE_NAME}'...")
        try:
            r = self.session.get(f"{FGA_BASE_URL}/stores", timeout=10)
            r.raise_for_status()
            for store in r.json().get("stores", []):
                if store.get("name") == FGA_STORE_NAME:
                    self.store_id = store["id"]
                    self.log(f"✓ Found existing store with ID: {self.store_id}")
                    return True
        except requests.exceptions.RequestException:
            pass

        try:
            r = self.session.post(f"{FGA_BASE_URL}/stores", json={"name": FGA_STORE_NAME}, timeout=10)
            r.raise_for_status()
            self.store_id = r.json().get("id")
            if not self.store_id:
                self.log("ERROR: No store ID in response")
                return False
            self.log(f"✓ Store created with ID: {self.store_id}")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create store", exc)
            return False

    # -- DSL parsing -------------------------------------------------------

    def parse_dsl_model(self, dsl_content: str) -> Dict[str, Any]:
        """Parse OpenFGA DSL model format into JSON format."""
        try:
            lines = dsl_content.strip().split("\n")
            schema_version = "1.1"
            type_definitions: List[Dict[str, Any]] = []
            current_type: Optional[str] = None
            current_relations: List[Dict[str, Any]] = []

            for line in lines:
                stripped = line.strip()
                if not stripped or stripped == "model" or stripped == "relations":
                    continue

                if stripped.startswith("schema "):
                    schema_version = stripped.replace("schema ", "").strip()
                    continue

                if stripped.startswith("type "):
                    if current_type:
                        type_definitions.append(self._build_type_def(current_type, current_relations))
                    current_type = stripped.replace("type ", "").strip()
                    current_relations = []
                    continue

                if stripped.startswith("define ") and ":" in stripped:
                    rel_def = stripped.replace("define ", "").strip()
                    rel_name, rel_value = rel_def.split(":", 1)
                    parsed = self._parse_relation_definition(rel_value.strip())
                    current_relations.append({
                        "name": rel_name.strip(),
                        "def": parsed["userset"],
                        "metadata": parsed.get("metadata"),
                    })

            if current_type:
                type_definitions.append(self._build_type_def(current_type, current_relations))

            return {"schema_version": schema_version, "type_definitions": type_definitions}
        except Exception as exc:
            self.log(f"ERROR: Failed to parse DSL model: {exc}")
            traceback.print_exc()
            return {}

    @staticmethod
    def _build_type_def(type_name: str, relations: List[Dict[str, Any]]) -> Dict[str, Any]:
        type_def: Dict[str, Any] = {"type": type_name}
        if not relations:
            return type_def
        type_def["relations"] = {r["name"]: r["def"] for r in relations}
        metadata_rels = {r["name"]: r["metadata"] for r in relations if r.get("metadata")}
        if metadata_rels:
            type_def["metadata"] = {"relations": metadata_rels}
        return type_def

    def _parse_relation_definition(self, definition: str) -> Dict[str, Any]:
        definition = definition.strip()
        result: Dict[str, Any] = {"userset": {}, "metadata": None}

        # Direct assignment: [user] or [user, hotel#admin]
        direct_match = re.match(r"^\[([^\]]+)\]$", definition)
        if direct_match:
            directly_related = self._parse_type_list(direct_match.group(1))
            result["userset"] = {"this": {}}
            result["metadata"] = {"directly_related_user_types": directly_related}
            return result

        # Union: A or B or C
        if " or " in definition:
            parts = definition.split(" or ")
            children = []
            all_directly_related = []
            for part in parts:
                parsed = self._parse_single_relation(part.strip())
                children.append(parsed["userset"])
                if parsed.get("directly_related"):
                    all_directly_related.extend(parsed["directly_related"])
            result["userset"] = {"union": {"child": children}}
            if all_directly_related:
                result["metadata"] = {"directly_related_user_types": all_directly_related}
            return result

        # Single relation
        parsed = self._parse_single_relation(definition)
        result["userset"] = parsed["userset"]
        if parsed.get("directly_related"):
            result["metadata"] = {"directly_related_user_types": parsed["directly_related"]}
        return result

    def _parse_single_relation(self, part: str) -> Dict[str, Any]:
        part = part.strip()

        direct_match = re.match(r"^\[([^\]]+)\]$", part)
        if direct_match:
            return {"userset": {"this": {}}, "directly_related": self._parse_type_list(direct_match.group(1))}

        from_match = re.match(r"^(\w+)\s+from\s+(\w+)$", part)
        if from_match:
            return {
                "userset": {
                    "tupleToUserset": {
                        "tupleset": {"relation": from_match.group(2)},
                        "computedUserset": {"relation": from_match.group(1)},
                    }
                }
            }

        return {"userset": {"computedUserset": {"relation": part}}}

    @staticmethod
    def _parse_type_list(types_str: str) -> List[Dict[str, str]]:
        result = []
        for t in types_str.split(","):
            t = t.strip()
            if "#" in t:
                type_name, relation = t.split("#", 1)
                result.append({"type": type_name.strip(), "relation": relation.strip()})
            else:
                result.append({"type": t})
        return result

    # -- Authorization model -----------------------------------------------

    def create_authorization_model(self, model_dsl: str) -> bool:
        self.log("Checking for existing authorization model...")
        try:
            model_json = self.parse_dsl_model(model_dsl)
            if not model_json.get("type_definitions"):
                self.log("ERROR: Failed to parse model DSL — no type definitions")
                return False

            existing_id = self._find_existing_authorization_model(model_json)
            if existing_id:
                self.authorization_model_id = existing_id
                self.log(f"✓ Using existing authorization model with ID: {existing_id}")
                return True

            self.log("Creating new authorization model...")
            r = self.session.post(
                f"{FGA_BASE_URL}/stores/{self.store_id}/authorization-models",
                json=model_json,
                timeout=10,
            )
            r.raise_for_status()
            self.authorization_model_id = r.json().get("authorization_model_id")
            if not self.authorization_model_id:
                self.log("ERROR: No authorization_model_id in response")
                return False
            self.log(f"✓ Authorization model created with ID: {self.authorization_model_id}")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create authorization model", exc)
            return False

    def _find_existing_authorization_model(self, new_model: Dict[str, Any]) -> Optional[str]:
        try:
            r = self.session.get(
                f"{FGA_BASE_URL}/stores/{self.store_id}/authorization-models",
                timeout=10,
            )
            r.raise_for_status()
            existing_models = r.json().get("authorization_models", [])
            if not existing_models:
                return None

            new_types_json = json.dumps(
                self._normalize_type_definitions(new_model.get("type_definitions", [])),
                sort_keys=True,
            )
            for existing in existing_models:
                existing_json = json.dumps(
                    self._normalize_type_definitions(existing.get("type_definitions", [])),
                    sort_keys=True,
                )
                if new_types_json == existing_json:
                    return existing.get("id")
            return None
        except requests.exceptions.RequestException:
            return None

    def _normalize_type_definitions(self, type_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for td in sorted(type_defs, key=lambda x: x.get("type", "")):
            norm: Dict[str, Any] = {"type": td.get("type")}
            if td.get("relations"):
                norm["relations"] = {
                    name: self._normalize_userset(udef)
                    for name, udef in td["relations"].items()
                }
            if td.get("metadata") and td["metadata"].get("relations"):
                meta_rels = {}
                for rn, rm in td["metadata"]["relations"].items():
                    if rm and rm.get("directly_related_user_types"):
                        drut = [
                            {k: v for k, v in item.items() if k in ("type", "relation") and v}
                            for item in rm["directly_related_user_types"]
                        ]
                        if drut:
                            meta_rels[rn] = {
                                "directly_related_user_types": sorted(drut, key=lambda x: (x.get("type", ""), x.get("relation", "")))
                            }
                if meta_rels:
                    norm["metadata"] = {"relations": meta_rels}
            normalized.append(norm)
        return normalized

    def _normalize_userset(self, userset: Dict[str, Any]) -> Dict[str, Any]:
        if not userset:
            return {}
        result: Dict[str, Any] = {}
        if "this" in userset:
            result["this"] = {}
        if "computedUserset" in userset:
            result["computedUserset"] = {"relation": userset["computedUserset"].get("relation", "")}
        if "tupleToUserset" in userset:
            ttu = userset["tupleToUserset"]
            result["tupleToUserset"] = {
                "tupleset": {"relation": ttu.get("tupleset", {}).get("relation", "")},
                "computedUserset": {"relation": ttu.get("computedUserset", {}).get("relation", "")},
            }
        for key in ("union", "intersection"):
            if key in userset:
                result[key] = {"child": [self._normalize_userset(c) for c in userset[key].get("child", [])]}
        if "difference" in userset:
            result["difference"] = {
                "base": self._normalize_userset(userset["difference"].get("base", {})),
                "subtract": self._normalize_userset(userset["difference"].get("subtract", {})),
            }
        return result

    # -- Tuples ------------------------------------------------------------

    def write_tuples(self, tuples: List[Dict[str, str]]) -> bool:
        self.log(f"Writing {len(tuples)} relationship tuples...")
        if not tuples:
            self.log("No tuples to write")
            return True
        try:
            r = self.session.post(
                f"{FGA_BASE_URL}/stores/{self.store_id}/write",
                json={
                    "writes": {
                        "tuple_keys": [{"user": t["user"], "relation": t["relation"], "object": t["object"]} for t in tuples],
                        "on_duplicate": "ignore",
                    },
                    "authorization_model_id": self.authorization_model_id,
                },
                timeout=10,
            )
            r.raise_for_status()
            self.log(f"✓ {len(tuples)} relationship tuples written successfully")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to write tuples", exc)
            return False

    # -- Orchestration -----------------------------------------------------

    def run(self) -> bool:
        self.log("Starting OpenFGA authorization store initialization...")
        self.log("=" * 80)

        if not self.wait_for_fga_api():
            return False

        config = self._load_config()
        if not config:
            return False

        if not self.get_or_create_store():
            return False

        model_dsl = config.get("model", "")
        if not model_dsl:
            self.log("ERROR: No model found in configuration")
            return False
        if not self.create_authorization_model(model_dsl):
            return False

        tuples = config.get("tuples", [])
        if not self.write_tuples(tuples):
            return False

        self.log("=" * 80)
        self.log("✓ OpenFGA initialization completed successfully!")
        self.log("")
        self.log("Summary:")
        self.log(f"  - Store: {FGA_STORE_NAME} (ID: {self.store_id})")
        self.log(f"  - Authorization Model ID: {self.authorization_model_id}")
        self.log(f"  - Tuples written: {len(tuples)}")
        return True

    def _load_config(self) -> Optional[Dict[str, Any]]:
        self.log(f"Loading configuration from {FGA_CONFIG_FILE}...")
        try:
            with open(FGA_CONFIG_FILE, "r") as fh:
                config = yaml.safe_load(fh)
            self.log("✓ Configuration loaded successfully")
            return config
        except FileNotFoundError:
            self.log(f"ERROR: Configuration file not found: {FGA_CONFIG_FILE}")
            return None
        except yaml.YAMLError as exc:
            self.log(f"ERROR: Failed to parse YAML: {exc}")
            return None


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

def main():
    am = GraviteeInitializer()
    fga = OpenFGAInitializer()

    try:
        if not am.run():
            sys.exit(1)
    except KeyboardInterrupt:
        am.log("Initialization interrupted by user"); sys.exit(1)
    except Exception as exc:
        am.log(f"FATAL ERROR: {exc}"); traceback.print_exc(); sys.exit(1)

    try:
        if not fga.run():
            sys.exit(1)
    except KeyboardInterrupt:
        fga.log("Initialization interrupted by user"); sys.exit(1)
    except Exception as exc:
        fga.log(f"FATAL ERROR: {exc}"); traceback.print_exc(); sys.exit(1)

    # Link OpenFGA engine to AM domain
    try:
        if fga.store_id:
            am.log("=" * 80)
            am.log("Creating OpenFGA Authorization Engine in Access Management...")
            if not am.create_openfga_authorization_engine(fga.store_id, fga.authorization_model_id):
                sys.exit(1)
            am.log("✓ OpenFGA Authorization Engine configured in AM")
        else:
            am.log("WARNING: No OpenFGA store ID available, skipping authorization engine creation")
    except Exception as exc:
        am.log(f"FATAL ERROR creating authorization engine: {exc}"); traceback.print_exc(); sys.exit(1)

    am.log("✓ Access Management initialization completed")
    print("[INIT] ✓ All initialization completed successfully!", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
