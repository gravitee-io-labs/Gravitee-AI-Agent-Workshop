#!/usr/bin/env python3
"""
Gravitee Initialization Script
This script configures Gravitee Access Management (AM) with required settings.
"""

import json
import os
import sys
import time
import requests
from typing import Optional, Dict, Any

# Configuration
AM_BASE_URL = os.getenv("AM_BASE_URL", "http://localhost:8093")
AM_USERNAME = os.getenv("AM_USERNAME", "admin")
AM_PASSWORD = os.getenv("AM_PASSWORD", "adminadmin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")

DOMAIN_NAME = "gravitee"
APP_NAME = "Gravitee Hotels"
APP_CLIENT_ID = "gravitee-hotels"
APP_CLIENT_SECRET = "gravitee-hotels"
APP_REDIRECT_URIS = [
    "http://localhost:8002/",
    "https://oauth.pstmn.io/v1/callback"
]
APP_SCOPES = ["openid", "profile", "bookings"]

# MCP Server configuration
MCP_SERVER_NAME = "Hotel Booking MCP Server"
MCP_SERVER_DESCRIPTION = "Exposes hotel booking API tools via MCP with OAuth2 protection"
MCP_SERVER_RESOURCE_IDENTIFIERS = ["http://localhost:8082/hotels/mcp"]
MCP_TOOLS = [
    {
        "key": "getAccommodations",
        "description": "Search for available hotel accommodations by location",
        "scopes": []  # Public endpoint, no scopes required
    },
    {
        "key": "getBookings",
        "description": "Retrieve user's hotel bookings",
        "scopes": ["bookings"]
    },
    {
        "key": "makeBooking",
        "description": "Create a new hotel booking",
        "scopes": ["bookings"]
    },
    {
        "key": "deleteBooking",
        "description": "Delete a hotel booking (admin only)",
        "scopes": ["bookings"]
    }
]

# Users configuration
USERS = [
    {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@gravitee.io",
        "username": "john.doe@gravitee.io",
        "password": "HelloWorld@123"
    },
    {
        "first_name": "Tom",
        "last_name": "Smith",
        "email": "tom.smith@gravitee.io",
        "username": "tom.smith@gravitee.io",
        "password": "HelloWorld@123"
    }
]

MAX_RETRIES = 30
RETRY_DELAY = 5


def get_bool_env(name: str, default: bool = False) -> bool:
    """Return boolean value from env var."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# OpenFGA Authorization Engine configuration
ENABLE_OPENFGA_ENGINE = get_bool_env("ENABLE_OPENFGA_ENGINE", True)
OPENFGA_ENGINE_NAME = os.getenv("OPENFGA_ENGINE_NAME", "OpenFGA Authorization Engine")
OPENFGA_ENGINE_TYPE = os.getenv("OPENFGA_ENGINE_TYPE", "openfga")
OPENFGA_CONNECTION_URI = os.getenv("OPENFGA_CONNECTION_URI", "http://openfga:8080")
OPENFGA_STORE_ID = os.getenv("OPENFGA_STORE_ID")
OPENFGA_STORE_ID_FILE = os.getenv("OPENFGA_STORE_ID_FILE", "/openfga/store_id")
OPENFGA_AUTHORIZATION_MODEL_ID = os.getenv("OPENFGA_AUTHORIZATION_MODEL_ID")
OPENFGA_API_TOKEN = os.getenv("OPENFGA_API_TOKEN")
OPENFGA_TOKEN_ISSUER = os.getenv("OPENFGA_TOKEN_ISSUER")
OPENFGA_API_AUDIENCE = os.getenv("OPENFGA_API_AUDIENCE")
OPENFGA_CLIENT_ID = os.getenv("OPENFGA_CLIENT_ID")
OPENFGA_CLIENT_SECRET = os.getenv("OPENFGA_CLIENT_SECRET")


class GraviteeInitializer:
    """Handles Gravitee Access Management initialization"""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.domain_id: Optional[str] = None
        self.app_id: Optional[str] = None
        self.mcp_server_id: Optional[str] = None
        self.mcp_server_client_id: Optional[str] = None
        self.mcp_server_client_secret: Optional[str] = None
        self.openfga_engine_id: Optional[str] = None
        self.openfga_store_id: Optional[str] = None
        self.openfga_model_id: Optional[str] = None
        self.domain_already_enabled: bool = False
        self.session = requests.Session()

    def log(self, message: str):
        """Print log message with prefix"""
        print(f"[GRAVITEE-INIT] {message}", flush=True)

    def wait_for_am_api(self):
        """Wait for AM Management API to be ready"""
        self.log("Waiting for Access Management API to be ready...")
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}",
                    timeout=5
                )
                if response.status_code in [200, 401]:  # 401 means API is up but needs auth
                    self.log("Access Management API is ready!")
                    return True
            except requests.exceptions.RequestException as e:
                self.log(f"Attempt {attempt}/{MAX_RETRIES}: AM API not ready yet ({e})")
            
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        
        self.log("ERROR: Access Management API did not become ready in time")
        return False

    def authenticate(self) -> bool:
        """Authenticate and get access token"""
        self.log("Authenticating with Access Management...")
        
        try:
            response = self.session.post(
                f"{AM_BASE_URL}/management/auth/token",
                auth=(AM_USERNAME, AM_PASSWORD),
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            self.access_token = data.get("access_token")
            
            if not self.access_token:
                self.log("ERROR: No access token in response")
                return False
            
            # Set authorization header for all future requests
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            })
            
            self.log("✓ Successfully authenticated")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Authentication failed: {e}")
            return False

    def create_domain(self) -> bool:
        """Create a new security domain"""
        self.log(f"Creating security domain '{DOMAIN_NAME}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains"
        
        payload = {
            "name": DOMAIN_NAME,
            "description": "Security domain for Gravitee Hotels application",
            "dataPlaneId": "default"
        }
        
        try:
            response = self.session.post(url, json=payload, timeout=10)
            
            # Check if domain already exists
            if response.status_code == 400:
                error_data = response.json()
                if "already exists" in str(error_data).lower():
                    self.log(f"Domain '{DOMAIN_NAME}' already exists, fetching it...")
                    return self.get_existing_domain()
            
            response.raise_for_status()
            data = response.json()
            self.domain_id = data.get("id")
            
            if not self.domain_id:
                self.log("ERROR: No domain ID in response")
                return False
            
            self.log(f"✓ Domain created with ID: {self.domain_id}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create domain: {e}")
            if hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def get_existing_domain(self) -> bool:
        """Get existing domain by name"""
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains"
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            domains = response.json()
            if isinstance(domains, dict) and 'data' in domains:
                domains = domains['data']
            
            for domain in domains:
                if domain.get("name") == DOMAIN_NAME:
                    self.domain_id = domain.get("id")
                    self.domain_already_enabled = domain.get("enabled", False)
                    self.log(f"✓ Found existing domain with ID: {self.domain_id} (enabled: {self.domain_already_enabled})")
                    return True
            
            self.log(f"ERROR: Domain '{DOMAIN_NAME}' not found")
            return False
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to get domains: {e}")
            return False

    def enable_domain(self) -> bool:
        """Enable the security domain"""
        if self.domain_already_enabled:
            self.log(f"✓ Domain '{DOMAIN_NAME}' is already enabled, skipping enable step")
            return True
        
        self.log(f"Enabling domain '{DOMAIN_NAME}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}"
        
        payload = {
            "enabled": True
        }
        
        try:
            response = self.session.patch(url, json=payload, timeout=10)
            response.raise_for_status()
            
            self.log("✓ Domain enabled successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to enable domain: {e}")
            if hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def check_domain_status(self) -> bool:
        """Check if domain exists and is enabled"""
        self.log(f"Checking domain status...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}"
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            name = data.get("name")
            enabled = data.get("enabled", False)
            
            if name == DOMAIN_NAME and enabled:
                self.log(f"✓ Domain '{DOMAIN_NAME}' exists and is enabled")
                return True
            else:
                self.log(f"WARNING: Domain exists but enabled={enabled}")
                return enabled
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to check domain status: {e}")
            return False

    def configure_dcr_settings(self) -> bool:
        """Configure DCR (Dynamic Client Registration) settings"""
        self.log("Configuring DCR settings...")

        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}"

        payload = {
            "oidc": {
                "clientRegistrationSettings": {
                    "allowLocalhostRedirectUri": True,
                    "allowHttpSchemeRedirectUri": True
                }
            }
        }

        try:
            response = self.session.patch(url, json=payload, timeout=10)
            response.raise_for_status()

            self.log("✓ DCR settings configured (localhost and HTTP redirect URIs allowed)")
            return True

        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to configure DCR settings: {e}")
            if hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def create_scopes(self) -> bool:
        """Create custom OAuth2 scopes for MCP tools"""
        self.log("Creating custom OAuth2 scopes...")

        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/scopes"

        # Extract unique scopes from MCP_TOOLS configuration
        unique_scopes = set()
        for tool in MCP_TOOLS:
            unique_scopes.update(tool.get("scopes", []))

        # Define custom scopes dynamically from MCP tools
        # Note: 'accommodations' scope removed - GET /accommodations is now a public endpoint
        scopes_to_create = [
            {
                "key": "bookings",
                "name": "Bookings",
                "description": "Access to manage hotel bookings (view, create, and delete)",
                "discovery": True,
                "parameterized": False
            }
        ]

        created_scopes = []
        for scope in scopes_to_create:
            try:
                response = self.session.post(url, json=scope, timeout=10)

                # Check if scope already exists
                if response.status_code == 400:
                    error_data = response.json()
                    error_message = str(error_data).lower()
                    if "already exists" in error_message:
                        self.log(f"  - Scope '{scope['key']}' already exists, skipping")
                        created_scopes.append(scope['key'])
                        continue

                response.raise_for_status()
                self.log(f"  - ✓ Created scope: {scope['key']}")
                created_scopes.append(scope['key'])

            except requests.exceptions.RequestException as e:
                self.log(f"ERROR: Failed to create scope '{scope['key']}': {e}")
                if hasattr(e, 'response') and hasattr(e.response, 'text'):
                    self.log(f"Response: {e.response.text}")
                return False

        if len(created_scopes) == len(scopes_to_create):
            self.log(f"✓ All custom scopes created/verified: {', '.join(created_scopes)}")
            return True
        else:
            self.log(f"ERROR: Not all scopes were created successfully")
            return False

    def _read_openfga_store_id(self) -> Optional[str]:
        """Load OpenFGA store ID from environment or mounted file."""
        if OPENFGA_STORE_ID and OPENFGA_STORE_ID.strip():
            return OPENFGA_STORE_ID.strip()

        if OPENFGA_STORE_ID_FILE:
            try:
                with open(OPENFGA_STORE_ID_FILE, "r", encoding="utf-8") as f:
                    store_id = f.read().strip()
                    if store_id:
                        return store_id
                    self.log(f"ERROR: OpenFGA store ID file '{OPENFGA_STORE_ID_FILE}' is empty")
            except FileNotFoundError:
                self.log(f"ERROR: OpenFGA store ID file '{OPENFGA_STORE_ID_FILE}' not found")
            except OSError as e:
                self.log(f"ERROR: Unable to read OpenFGA store ID file '{OPENFGA_STORE_ID_FILE}': {e}")

        return None

    def _build_openfga_configuration(self, store_id: str, authorization_model_id: Optional[str]) -> Dict[str, Any]:
        """Build configuration payload sent to AM."""
        config: Dict[str, Any] = {
            "connectionUri": OPENFGA_CONNECTION_URI,
            "storeId": store_id
        }

        if authorization_model_id:
            config["authorizationModelId"] = authorization_model_id

        optional_fields = {
            "apiToken": OPENFGA_API_TOKEN,
            "tokenIssuer": OPENFGA_TOKEN_ISSUER,
            "apiAudience": OPENFGA_API_AUDIENCE,
            "clientId": OPENFGA_CLIENT_ID,
            "clientSecret": OPENFGA_CLIENT_SECRET,
        }

        for key, value in optional_fields.items():
            if value:
                config[key] = value

        return config

    @staticmethod
    def _normalize_openfga_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Strip empty values for deterministic comparison."""
        normalized = {}
        for key, value in config.items():
            if value in (None, "", []):
                continue
            normalized[key] = value
        return normalized

    def _list_authorization_engines(self) -> Optional[list]:
        """Return existing authorization engines for the domain."""
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/authorization-engines"
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            engines = response.json()
            if isinstance(engines, dict) and "data" in engines:
                engines = engines["data"]
            if not isinstance(engines, list):
                engines = []
            return engines
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to list authorization engines: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return None

    def _fetch_openfga_authorization_model_id(self, store_id: str) -> Optional[str]:
        """Query OpenFGA for the latest authorization model ID."""
        base_url = OPENFGA_CONNECTION_URI.rstrip("/")
        url = f"{base_url}/stores/{store_id}/authorization-models"
        params = {"page_size": 1}
        headers = {"Accept": "application/json"}
        if OPENFGA_API_TOKEN:
            headers["Authorization"] = f"Bearer {OPENFGA_API_TOKEN}"

        try:
            response = self.session.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            models = data.get("authorization_models") or data.get("authorizationModels") or []
            if models:
                model_id = models[0].get("id") or models[0].get("authorization_model_id")
                if model_id:
                    self.log(f"✓ Retrieved OpenFGA authorization model ID: {model_id}")
                    return model_id
            self.log("ERROR: OpenFGA returned no authorization models")
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to query OpenFGA authorization models: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
        except ValueError as e:
            self.log(f"ERROR: Failed to parse OpenFGA authorization models response: {e}")
        return None

    def ensure_openfga_authorization_engine(self) -> bool:
        """Create or update the OpenFGA Authorization Engine."""
        if not ENABLE_OPENFGA_ENGINE:
            self.log("OpenFGA Authorization Engine provisioning disabled via ENABLE_OPENFGA_ENGINE")
            return True

        if not self.domain_id:
            self.log("ERROR: Domain ID is not set; cannot configure OpenFGA Authorization Engine")
            return False

        if not OPENFGA_CONNECTION_URI:
            self.log("ERROR: OPENFGA_CONNECTION_URI is not configured")
            return False

        store_id = self._read_openfga_store_id()
        if not store_id:
            self.log("ERROR: Unable to determine OpenFGA store ID. Ensure openfga-bootstrap completed successfully.")
            return False

        authorization_model_id = OPENFGA_AUTHORIZATION_MODEL_ID
        if not authorization_model_id:
            authorization_model_id = self._fetch_openfga_authorization_model_id(store_id)
            if not authorization_model_id:
                self.log("ERROR: Unable to determine OpenFGA authorization model ID")
                return False

        config = self._build_openfga_configuration(store_id, authorization_model_id)
        desired_config = self._normalize_openfga_config(config)

        engines = self._list_authorization_engines()
        if engines is None:
            return False

        existing_engine = next((engine for engine in engines if engine.get("type") == OPENFGA_ENGINE_TYPE), None)

        payload_config = json.dumps(config)

        if existing_engine:
            engine_id = existing_engine.get("id")
            current_name = existing_engine.get("name")
            raw_config = existing_engine.get("configuration") or "{}"
            try:
                parsed_config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
            except ValueError:
                parsed_config = {}
            normalized_existing = self._normalize_openfga_config(parsed_config if isinstance(parsed_config, dict) else {})

            needs_update = normalized_existing != desired_config or current_name != OPENFGA_ENGINE_NAME
            if not needs_update:
                self.log("✓ OpenFGA Authorization Engine already configured")
                self.openfga_engine_id = engine_id
                self.openfga_store_id = store_id
                self.openfga_model_id = authorization_model_id
                return True

            url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/authorization-engines/{engine_id}"
            update_payload = {
                "name": OPENFGA_ENGINE_NAME,
                "configuration": payload_config
            }

            try:
                response = self.session.put(url, json=update_payload, timeout=10)
                response.raise_for_status()
                self.log("✓ Updated OpenFGA Authorization Engine configuration")
                self.openfga_engine_id = engine_id
                self.openfga_store_id = store_id
                self.openfga_model_id = authorization_model_id
                return True
            except requests.exceptions.RequestException as e:
                self.log(f"ERROR: Failed to update OpenFGA Authorization Engine: {e}")
                if hasattr(e, 'response') and hasattr(e.response, 'text'):
                    self.log(f"Response: {e.response.text}")
                return False

        # No existing engine, create a new one
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/authorization-engines"
        create_payload = {
            "type": OPENFGA_ENGINE_TYPE,
            "name": OPENFGA_ENGINE_NAME,
            "configuration": payload_config
        }

        try:
            response = self.session.post(url, json=create_payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.openfga_engine_id = data.get("id")
            self.openfga_store_id = store_id
            self.openfga_model_id = authorization_model_id
            self.log("✓ Created OpenFGA Authorization Engine")
            return True
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create OpenFGA Authorization Engine: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def create_application(self) -> bool:
        """Create the application"""
        self.log(f"Creating application '{APP_NAME}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications"
        
        payload = {
            "name": APP_NAME,
            "type": "BROWSER",
            "clientId": APP_CLIENT_ID,
            "clientSecret": APP_CLIENT_SECRET,
            "redirectUris": APP_REDIRECT_URIS
        }
        
        try:
            response = self.session.post(url, json=payload, timeout=10)
            
            # Check if app already exists
            if response.status_code == 400:
                error_data = response.json()
                if "already exists" in str(error_data).lower() or "clientId" in str(error_data).lower():
                    self.log(f"Application with client ID '{APP_CLIENT_ID}' already exists, fetching it...")
                    return self.get_existing_application()
            
            response.raise_for_status()
            data = response.json()
            self.app_id = data.get("id")
            
            if not self.app_id:
                self.log("ERROR: No application ID in response")
                return False
            
            self.log(f"✓ Application created with ID: {self.app_id}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create application: {e}")
            if hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def get_existing_application(self) -> bool:
        """Get existing application by client ID"""
        # First, search for applications using query parameter
        search_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications"
        params = {"q": APP_CLIENT_ID}
        
        try:
            response = self.session.get(search_url, params=params, timeout=10)
            response.raise_for_status()
            
            apps = response.json()
            if isinstance(apps, dict) and 'data' in apps:
                apps = apps['data']
            
            # For each app, fetch full details and check the OAuth clientId
            for app in apps:
                app_id = app.get("id")
                if not app_id:
                    continue
                
                # Get full application details
                detail_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{app_id}"
                
                try:
                    detail_response = self.session.get(detail_url, timeout=10)
                    detail_response.raise_for_status()
                    
                    app_data = detail_response.json()
                    settings = app_data.get("settings", {})
                    oauth = settings.get("oauth", {})
                    
                    if oauth.get("clientId") == APP_CLIENT_ID:
                        self.app_id = app_id
                        self.log(f"✓ Found existing application with ID: {self.app_id}")
                        return True
                        
                except requests.exceptions.RequestException as e:
                    self.log(f"WARNING: Failed to get details for app {app_id}: {e}")
                    continue
            
            self.log(f"ERROR: Application with client ID '{APP_CLIENT_ID}' not found")
            return False
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to search applications: {e}")
            return False

    def add_scopes_to_application(self) -> bool:
        """Add scopes to the application"""
        self.log(f"Adding scopes {APP_SCOPES} to application...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{self.app_id}"
        
        # Build scopeSettings array
        scope_settings = []
        for scope in APP_SCOPES:
            scope_settings.append({
                "scope": scope,
                "defaultScope": False,
                "scopeApproval": 300
            })
        
        # Minimal payload with just the scopes
        payload = {
            "settings": {
                "oauth": {
                    "scopeSettings": scope_settings
                }
            }
        }
        
        try:
            response = self.session.put(url, json=payload, timeout=10)
            response.raise_for_status()
            
            self.log(f"✓ Scopes {APP_SCOPES} added to application")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to add scopes: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def add_identity_provider_to_application(self) -> bool:
        """Add the default identity provider to the application"""
        self.log("Adding default identity provider to application...")
        
        # First, get the list of identity providers for the domain
        idp_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/identities"
        
        try:
            response = self.session.get(idp_url, timeout=10)
            response.raise_for_status()
            
            idps = response.json()
            
            # Find the system identity provider
            system_idp_id = None
            for idp in idps:
                if idp.get("system") is True:
                    system_idp_id = idp.get("id")
                    idp_name = idp.get("name", "Unknown")
                    self.log(f"Found system identity provider: {idp_name} (ID: {system_idp_id})")
                    break
            
            if not system_idp_id:
                self.log("ERROR: No system identity provider found")
                return False
            
            # Now add the identity provider to the application
            app_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{self.app_id}"
            
            payload = {
                "identityProviders": [
                    {
                        "identity": system_idp_id,
                        "selectionRule": "",
                        "priority": 0
                    }
                ]
            }
            
            put_response = self.session.put(app_url, json=payload, timeout=10)
            put_response.raise_for_status()
            
            self.log("✓ Identity provider added to application")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to add identity provider: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def configure_token_claims(self) -> bool:
        """Configure custom claims for access tokens and ID tokens"""
        self.log("Configuring custom token claims (access token + ID token)...")

        # PATCH only the settings we want to change
        patch_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{self.app_id}"

        payload = {
            "settings": {
                "oauth": {
                    "tokenCustomClaims": [
                        # Access token claims
                        {
                            "tokenType": "ACCESS_TOKEN",
                            "claimName": "user_email",
                            "claimValue": "{#context.attributes['user']['email']}"
                        },
                        {
                            "tokenType": "ACCESS_TOKEN",
                            "claimName": "preferred_username",
                            "claimValue": "{#context.attributes['user']['username']}"
                        },
                        # ID token claims for user display
                        {
                            "tokenType": "ID_TOKEN",
                            "claimName": "preferred_username",
                            "claimValue": "{#context.attributes['user']['username']}"
                        },
                        {
                            "tokenType": "ID_TOKEN",
                            "claimName": "given_name",
                            "claimValue": "{#context.attributes['user']['firstName']}"
                        },
                        {
                            "tokenType": "ID_TOKEN",
                            "claimName": "family_name",
                            "claimValue": "{#context.attributes['user']['lastName']}"
                        }
                    ]
                }
            }
        }

        try:
            # Use PATCH to only update specific fields
            update_response = self.session.patch(patch_url, json=payload, timeout=10)
            update_response.raise_for_status()

            self.log("✓ Custom token claims configured (user_email, preferred_username)")
            return True

        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to configure token claims: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def create_users(self) -> bool:
        """Create all users in the domain"""
        self.log(f"Creating {len(USERS)} user(s)...")

        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/users"

        all_success = True
        for user in USERS:
            username = user["username"]
            self.log(f"  Creating user '{username}'...")

            payload = {
                "firstName": user["first_name"],
                "lastName": user["last_name"],
                "email": user["email"],
                "username": user["username"],
                "password": user["password"],
                "forceResetPassword": False,
                "preRegistration": False
            }

            try:
                response = self.session.post(url, json=payload, timeout=10)

                # Check if user already exists
                if response.status_code == 400:
                    error_data = response.json()
                    error_message = error_data.get("message", "")
                    if "already exists" in error_message.lower():
                        self.log(f"  ✓ User '{username}' already exists, skipping creation")
                        continue

                response.raise_for_status()

                self.log(f"  ✓ User '{username}' created successfully")

            except requests.exceptions.RequestException as e:
                self.log(f"  ERROR: Failed to create user '{username}': {e}")
                if hasattr(e, 'response') and hasattr(e.response, 'text'):
                    self.log(f"  Response: {e.response.text}")
                all_success = False

        if all_success:
            self.log(f"✓ All users created/verified")

        return all_success

    def register_mcp_server(self) -> bool:
        """Register MCP Server as a Protected Resource"""
        self.log(f"Registering MCP Server '{MCP_SERVER_NAME}'...")

        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/protected-resources"

        # Build MCP tools with proper structure
        features = []
        for tool in MCP_TOOLS:
            features.append({
                "type": "MCP_TOOL",
                "key": tool["key"],
                "description": tool["description"],
                "scopes": tool["scopes"]
            })

        payload = {
            "name": MCP_SERVER_NAME,
            "description": MCP_SERVER_DESCRIPTION,
            "type": "MCP_SERVER",
            "resourceIdentifiers": MCP_SERVER_RESOURCE_IDENTIFIERS,
            "features": features
        }

        try:
            response = self.session.post(url, json=payload, timeout=10)

            # Check if MCP server already exists
            if response.status_code == 400:
                error_data = response.json()
                error_message = str(error_data).lower()
                if "already exists" in error_message or "resource identifier" in error_message:
                    self.log(f"MCP Server with resource identifiers {MCP_SERVER_RESOURCE_IDENTIFIERS} already exists, fetching it...")
                    return self.get_existing_mcp_server()

            response.raise_for_status()
            data = response.json()

            self.mcp_server_id = data.get("id")
            self.mcp_server_client_id = data.get("clientId")
            self.mcp_server_client_secret = data.get("clientSecret")

            if not self.mcp_server_id or not self.mcp_server_client_id:
                self.log("ERROR: Missing MCP server ID or client ID in response")
                return False

            self.log(f"✓ MCP Server registered successfully")
            self.log(f"  - MCP Server ID: {self.mcp_server_id}")
            self.log(f"  - Client ID: {self.mcp_server_client_id}")
            if self.mcp_server_client_secret:
                self.log(f"  - Client Secret: {self.mcp_server_client_secret[:10]}... (saved)")
            self.log(f"  - Tools: {', '.join([tool['key'] for tool in MCP_TOOLS])}")

            return True

        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to register MCP server: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def get_existing_mcp_server(self) -> bool:
        """Get existing MCP server by resource identifier"""
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/protected-resources"
        params = {"type": "MCP_SERVER"}

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            result = response.json()
            mcp_servers = result.get("data", [])

            # Find MCP server by resource identifier
            for server in mcp_servers:
                server_identifiers = server.get("resourceIdentifiers", [])
                if any(identifier in MCP_SERVER_RESOURCE_IDENTIFIERS for identifier in server_identifiers):
                    self.mcp_server_id = server.get("id")
                    self.mcp_server_client_id = server.get("clientId")
                    # Note: clientSecret is not returned for existing servers
                    self.mcp_server_client_secret = None

                    self.log(f"✓ Found existing MCP Server")
                    self.log(f"  - MCP Server ID: {self.mcp_server_id}")
                    self.log(f"  - Client ID: {self.mcp_server_client_id}")
                    self.log(f"  - Client Secret: (not available for existing servers)")

                    return True

            self.log(f"ERROR: MCP Server with resource identifiers {MCP_SERVER_RESOURCE_IDENTIFIERS} not found")
            return False

        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to get MCP servers: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def run(self) -> bool:
        """Run the initialization process"""
        self.log("Starting Gravitee Access Management initialization...")
        self.log("=" * 80)
        
        # Wait for AM API to be ready
        if not self.wait_for_am_api():
            return False
        
        # Step 1: Authenticate
        if not self.authenticate():
            return False
        
        # Step 2: Create domain
        if not self.create_domain():
            return False
        
        # Step 3: Enable domain
        if not self.enable_domain():
            return False
        
        # Step 4: Configure DCR settings
        if not self.configure_dcr_settings():
            return False

        # Step 5: Create custom OAuth2 scopes
        if not self.create_scopes():
            return False

        # Step 6: Configure OpenFGA Authorization Engine
        if not self.ensure_openfga_authorization_engine():
            return False

        # Step 7: Create application
        if not self.create_application():
            return False

        # Step 8: Add scopes to application
        if not self.add_scopes_to_application():
            return False

        # Step 9: Add identity provider to application
        if not self.add_identity_provider_to_application():
            return False

        # Step 10: Configure custom token claims
        if not self.configure_token_claims():
            return False

        # Step 11: Create users
        if not self.create_users():
            return False

        # Step 12: Register MCP Server
        if not self.register_mcp_server():
            return False

        self.log("=" * 80)
        self.log("✓ Access Management initialization completed successfully!")
        self.log("")
        self.log("Summary:")
        self.log(f"  - Domain: {DOMAIN_NAME} (ID: {self.domain_id})")
        if self.openfga_engine_id:
            self.log(f"  - Authorization Engine: {OPENFGA_ENGINE_NAME} (ID: {self.openfga_engine_id})")
            self.log(f"    - Connection URI: {OPENFGA_CONNECTION_URI}")
            if self.openfga_store_id:
                self.log(f"    - Store ID: {self.openfga_store_id}")
            if self.openfga_model_id:
                self.log(f"    - Authorization Model ID: {self.openfga_model_id}")
        self.log(f"  - Application: {APP_NAME} (ID: {self.app_id})")
        self.log(f"    - Client ID: {APP_CLIENT_ID}")
        self.log(f"    - Client Secret: {APP_CLIENT_SECRET}")
        self.log(f"    - Redirect URIs: {', '.join(APP_REDIRECT_URIS)}")
        self.log(f"    - Scopes: {', '.join(APP_SCOPES)}")
        self.log(f"  - MCP Server: {MCP_SERVER_NAME} (ID: {self.mcp_server_id})")
        self.log(f"    - Client ID: {self.mcp_server_client_id}")
        if self.mcp_server_client_secret:
            self.log(f"    - Client Secret: {self.mcp_server_client_secret}")
        self.log(f"    - Resource URL: {MCP_SERVER_RESOURCE_IDENTIFIERS[0]}")
        self.log(f"    - Tools: {', '.join([tool['key'] for tool in MCP_TOOLS])}")
        self.log(f"  - Users:")
        for user in USERS:
            self.log(f"    - {user['email']}")

        self.log("")
        self.log("NOTE: Frontend is configured to use MCP Resource URL (RFC 8707):")
        self.log(f"  MCP_SERVER_RESOURCE={MCP_SERVER_RESOURCE_IDENTIFIERS[0]}")

        # Save MCP Server credentials to file for token introspection
        self._save_mcp_credentials()

        return True

    def _save_mcp_credentials(self):
        """Save MCP Server credentials to file for use by hotel-booking-api"""
        credentials_file = os.getenv("MCP_CREDENTIALS_FILE", "/mcp-credentials/credentials")

        if not self.mcp_server_client_id:
            self.log("WARNING: MCP Server client ID not available, skipping credentials file creation")
            return

        try:
            os.makedirs(os.path.dirname(credentials_file), exist_ok=True)

            with open(credentials_file, 'w') as f:
                f.write(f"# MCP Server credentials for token introspection\n")
                f.write(f"# Generated by gravitee-init on {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"MCP_SERVER_CLIENT_ID={self.mcp_server_client_id}\n")
                if self.mcp_server_client_secret:
                    f.write(f"MCP_SERVER_CLIENT_SECRET={self.mcp_server_client_secret}\n")
                else:
                    f.write(f"# MCP_SERVER_CLIENT_SECRET=<not available for existing servers>\n")

            self.log(f"✓ MCP Server credentials saved to {credentials_file}")
            self.log("  NOTE: Source this file in your .env or export these variables for token introspection")

        except Exception as e:
            self.log(f"WARNING: Failed to save MCP credentials to file: {e}")


def main():
    """Main entry point"""
    initializer = GraviteeInitializer()
    
    try:
        success = initializer.run()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        initializer.log("Initialization interrupted by user")
        sys.exit(1)
    except Exception as e:
        initializer.log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
