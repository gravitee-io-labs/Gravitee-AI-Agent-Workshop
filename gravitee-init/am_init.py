#!/usr/bin/env python3
"""
Gravitee Initialization Script
This script configures Gravitee Access Management (AM) with required settings.
Applications are loaded from external YAML configuration files.
"""

import os
import sys
import time
import glob
import requests
import yaml
from typing import Optional, Dict, Any, List

# Configuration
AM_BASE_URL = os.getenv("AM_BASE_URL", "http://localhost:8093")
AM_USERNAME = os.getenv("AM_USERNAME", "admin")
AM_PASSWORD = os.getenv("AM_PASSWORD", "adminadmin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")

DOMAIN_NAME = "gravitee"

# Directory containing application configuration files
APPS_CONFIG_DIR = os.getenv("APPS_CONFIG_DIR", "/app/am-apps")

# User configuration
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"
USER_EMAIL = "john.doe@gravitee.io"
USER_USERNAME = "john.doe@gravitee.io"
USER_PASSWORD = "HelloWorld@123"

MAX_RETRIES = 30
RETRY_DELAY = 5

# OpenFGA Configuration
FGA_BASE_URL = os.getenv("FGA_BASE_URL", "http://openfga:8080")
FGA_STORE_NAME = "Hotel Booking Authorization"
FGA_CONFIG_FILE = os.getenv("FGA_CONFIG_FILE", "/app/openfga/openfgastore.yaml")

# OpenFGA Authorization Engine Configuration for AM
OPENFGA_SERVER_URL = os.getenv("OPENFGA_SERVER_URL", "http://openfga:8080")

# MCP Servers Configuration Directory
MCP_SERVERS_CONFIG_DIR = os.getenv("MCP_SERVERS_CONFIG_DIR", "/app/am-mcp-servers")


class GraviteeInitializer:
    """Handles Gravitee Access Management initialization"""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.domain_id: Optional[str] = None
        self.apps: List[Dict[str, Any]] = []  # List of created apps
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

    def load_app_configs(self) -> List[Dict[str, Any]]:
        """Load all application configurations from YAML files"""
        self.log(f"Loading application configurations from {APPS_CONFIG_DIR}...")
        
        configs = []
        yaml_files = glob.glob(os.path.join(APPS_CONFIG_DIR, "*.yaml")) + \
                     glob.glob(os.path.join(APPS_CONFIG_DIR, "*.yml"))
        
        if not yaml_files:
            self.log(f"WARNING: No YAML files found in {APPS_CONFIG_DIR}")
            return configs
        
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and config.get('name'):
                        configs.append(config)
                        self.log(f"  Loaded config: {config.get('name')} from {os.path.basename(yaml_file)}")
            except Exception as e:
                self.log(f"WARNING: Failed to load {yaml_file}: {e}")
        
        self.log(f"✓ Loaded {len(configs)} application configuration(s)")
        return configs

    def create_application(self, app_config: Dict[str, Any]) -> Optional[str]:
        """Create an application from config and return its ID"""
        app_name = app_config.get('name')
        client_id = app_config.get('clientId')
        
        self.log(f"Creating application '{app_name}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications"
        
        payload = {
            "name": app_name,
            "type": app_config.get('type', 'BROWSER'),
            "clientId": client_id,
            "clientSecret": app_config.get('clientSecret'),
            "redirectUris": app_config.get('redirectUris', [])
        }
        
        # Add description if provided
        if app_config.get('description'):
            payload['description'] = app_config.get('description')
        
        try:
            response = self.session.post(url, json=payload, timeout=10)
            
            # Check if app already exists
            if response.status_code == 400:
                error_data = response.json()
                if "already exists" in str(error_data).lower() or "clientId" in str(error_data).lower():
                    self.log(f"Application with client ID '{client_id}' already exists, fetching it...")
                    return self.get_existing_application(client_id)
            
            response.raise_for_status()
            data = response.json()
            app_id = data.get("id")
            
            if not app_id:
                self.log("ERROR: No application ID in response")
                return None
            
            self.log(f"✓ Application created with ID: {app_id}")
            return app_id
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create application: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return None

    def get_existing_application(self, client_id: str) -> Optional[str]:
        """Get existing application by client ID and return its ID"""
        # First, search for applications using query parameter
        search_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications"
        params = {"q": client_id}
        
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
                    
                    if oauth.get("clientId") == client_id:
                        self.log(f"✓ Found existing application with ID: {app_id}")
                        return app_id
                        
                except requests.exceptions.RequestException as e:
                    self.log(f"WARNING: Failed to get details for app {app_id}: {e}")
                    continue
            
            self.log(f"ERROR: Application with client ID '{client_id}' not found")
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to search applications: {e}")
            return None

    def configure_application_settings(self, app_id: str, app_config: Dict[str, Any]) -> bool:
        """Configure application settings including scopes and custom token claims"""
        app_name = app_config.get('name')
        scopes = app_config.get('scopes', [])
        token_claims = app_config.get('tokenCustomClaims', [])
        
        self.log(f"Configuring settings for application '{app_name}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{app_id}"
        
        # Build scopeSettings array
        scope_settings = []
        for scope in scopes:
            scope_settings.append({
                "scope": scope,
                "defaultScope": False,
                "scopeApproval": 300
            })
        
        # Build tokenCustomClaims array
        custom_claims = []
        for claim in token_claims:
            custom_claims.append({
                "tokenType": claim.get('tokenType', 'ACCESS_TOKEN'),
                "claimName": claim.get('claimName'),
                "claimValue": claim.get('claimValue')
            })
        
        # Build the OAuth settings payload
        oauth_settings = {}
        if scope_settings:
            oauth_settings["scopeSettings"] = scope_settings
        if custom_claims:
            oauth_settings["tokenCustomClaims"] = custom_claims
        
        # If no settings to configure, skip
        if not oauth_settings:
            self.log(f"  No additional settings to configure for '{app_name}'")
            return True
        
        payload = {
            "settings": {
                "oauth": oauth_settings
            }
        }
        
        try:
            response = self.session.put(url, json=payload, timeout=10)
            response.raise_for_status()
            
            if scopes:
                self.log(f"  ✓ Scopes configured: {scopes}")
            if custom_claims:
                self.log(f"  ✓ Custom token claims configured: {[c.get('claimName') for c in custom_claims]}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to configure settings: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def add_identity_provider_to_application(self, app_id: str, app_name: str) -> bool:
        """Add the default identity provider to the application"""
        self.log(f"Adding default identity provider to '{app_name}'...")
        
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
                    self.log(f"  Found system identity provider: {idp_name}")
                    break
            
            if not system_idp_id:
                self.log("ERROR: No system identity provider found")
                return False
            
            # Now add the identity provider to the application
            app_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{app_id}"
            
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
            
            self.log(f"  ✓ Identity provider added to '{app_name}'")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to add identity provider: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def create_user(self) -> bool:
        """Create a user in the domain"""
        self.log(f"Creating user '{USER_USERNAME}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/users"
        
        payload = {
            "firstName": USER_FIRST_NAME,
            "lastName": USER_LAST_NAME,
            "email": USER_EMAIL,
            "username": USER_USERNAME,
            "password": USER_PASSWORD,
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
                    self.log(f"✓ User '{USER_USERNAME}' already exists, skipping creation")
                    return True
            
            response.raise_for_status()
            
            self.log(f"✓ User '{USER_USERNAME}' created successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create user: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def create_all_applications(self, app_configs: List[Dict[str, Any]]) -> bool:
        """Create and configure all applications from configs"""
        if not app_configs:
            self.log("WARNING: No application configurations to process")
            return True
        
        for app_config in app_configs:
            app_name = app_config.get('name')
            client_id = app_config.get('clientId')
            
            # Step 1: Create the application
            app_id = self.create_application(app_config)
            if not app_id:
                return False
            
            # Step 2: Configure settings (scopes and custom claims)
            if not self.configure_application_settings(app_id, app_config):
                return False
            
            # Step 3: Add identity provider
            if not self.add_identity_provider_to_application(app_id, app_name):
                return False
            
            # Store the created app info
            self.apps.append({
                "name": app_name,
                "id": app_id,
                "clientId": client_id,
                "clientSecret": app_config.get('clientSecret'),
                "type": app_config.get('type', 'BROWSER')
            })
            
            self.log(f"✓ Application '{app_name}' fully configured")
        
        return True

    def create_openfga_authorization_engine(self, store_id: str, authorization_model_id: str = None) -> bool:
        """Create an OpenFGA authorization engine in the domain"""
        self.log("Creating OpenFGA authorization engine...")
        
        # First, check if an authorization engine already exists
        list_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/authorization-engines"
        
        try:
            response = self.session.get(list_url, timeout=10)
            response.raise_for_status()
            
            engines = response.json()
            for engine in engines:
                if engine.get("type") == "openfga":
                    self.log(f"✓ OpenFGA authorization engine already exists with ID: {engine.get('id')}")
                    return True
        except requests.exceptions.RequestException as e:
            self.log(f"WARNING: Failed to list authorization engines: {e}")
        
        # Create the authorization engine
        import json
        
        configuration = {
            "connectionUri": OPENFGA_SERVER_URL,
            "storeId": store_id
        }
        
        # Add authorization model ID if provided
        if authorization_model_id:
            configuration["authorizationModelId"] = authorization_model_id
        
        payload = {
            "type": "openfga",
            "name": "OpenFGA Authorization Engine",
            "configuration": json.dumps(configuration)
        }
        
        try:
            response = self.session.post(list_url, json=payload, timeout=10)
            
            # Check if already exists
            if response.status_code == 400:
                error_data = response.json()
                if "already exists" in str(error_data).lower():
                    self.log("✓ OpenFGA authorization engine already exists")
                    return True
            
            response.raise_for_status()
            data = response.json()
            engine_id = data.get("id")
            
            self.log(f"✓ OpenFGA authorization engine created with ID: {engine_id}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create OpenFGA authorization engine: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def load_mcp_server_configs(self) -> List[Dict[str, Any]]:
        """Load all MCP server configurations from YAML files"""
        self.log(f"Loading MCP server configurations from {MCP_SERVERS_CONFIG_DIR}...")
        
        configs = []
        yaml_files = glob.glob(os.path.join(MCP_SERVERS_CONFIG_DIR, "*.yaml")) + \
                     glob.glob(os.path.join(MCP_SERVERS_CONFIG_DIR, "*.yml"))
        
        if not yaml_files:
            self.log(f"No MCP server YAML files found in {MCP_SERVERS_CONFIG_DIR}")
            return configs
        
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and config.get('name'):
                        configs.append(config)
                        self.log(f"  Loaded MCP server config: {config.get('name')} from {os.path.basename(yaml_file)}")
            except Exception as e:
                self.log(f"WARNING: Failed to load {yaml_file}: {e}")
        
        self.log(f"✓ Loaded {len(configs)} MCP server configuration(s)")
        return configs

    def create_mcp_server(self, mcp_config: Dict[str, Any]) -> Optional[str]:
        """Create an MCP Server (Protected Resource) and return its ID"""
        name = mcp_config.get('name')
        client_id = mcp_config.get('clientId')
        
        self.log(f"Creating MCP Server '{name}'...")
        
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/protected-resources"
        
        # Build the features (tools) array
        features = []
        for tool in mcp_config.get('tools', []):
            features.append({
                "key": tool.get('key'),
                "description": tool.get('description', ''),
                "type": tool.get('type', 'MCP_TOOL'),
                "scopes": tool.get('scopes', [])
            })
        
        payload = {
            "name": name,
            "description": mcp_config.get('description', ''),
            "resourceIdentifiers": mcp_config.get('resourceIdentifiers', []),
            "clientId": client_id,
            "clientSecret": mcp_config.get('clientSecret'),
            "type": mcp_config.get('type', 'MCP_SERVER'),
            "features": features
        }
        
        try:
            response = self.session.post(url, json=payload, timeout=10)
            
            # Check if already exists
            if response.status_code == 400:
                error_data = response.json()
                error_message = str(error_data)
                if "already exists" in error_message.lower() or "clientId" in error_message.lower():
                    self.log(f"MCP Server with client ID '{client_id}' may already exist, checking...")
                    return self.get_existing_mcp_server(client_id)
            
            response.raise_for_status()
            data = response.json()
            resource_id = data.get("id")
            
            if not resource_id:
                self.log("ERROR: No protected resource ID in response")
                return None
            
            self.log(f"✓ MCP Server '{name}' created with ID: {resource_id}")
            return resource_id
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create MCP Server: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return None

    def get_existing_mcp_server(self, client_id: str) -> Optional[str]:
        """Get existing MCP Server (Protected Resource) by client ID and return its ID"""
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/protected-resources"
        params = {"type": "MCP_SERVER"}
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            resources = data.get("data", [])
            
            for resource in resources:
                # clientId is available directly in list response
                if resource.get("clientId") == client_id:
                    resource_id = resource.get("id")
                    self.log(f"✓ Found existing MCP Server with ID: {resource_id}")
                    return resource_id
            
            self.log(f"MCP Server with client ID '{client_id}' not found")
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to search MCP Servers: {e}")
            return None

    def create_all_mcp_servers(self, mcp_configs: List[Dict[str, Any]]) -> bool:
        """Create all MCP Servers from configs"""
        if not mcp_configs:
            self.log("No MCP server configurations to process")
            return True
        
        for mcp_config in mcp_configs:
            name = mcp_config.get('name')
            client_id = mcp_config.get('clientId')
            
            # Create the MCP Server
            resource_id = self.create_mcp_server(mcp_config)
            if not resource_id:
                return False
            
            tools = mcp_config.get('tools', [])
            self.log(f"✓ MCP Server '{name}' configured with {len(tools)} tool(s)")
        
        return True

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
        
        # Step 5: Load application configurations
        app_configs = self.load_app_configs()
        
        # Step 6: Create and configure all applications
        if not self.create_all_applications(app_configs):
            return False
        
        # Step 7: Create user
        if not self.create_user():
            return False
        
        # Step 8: Load and create MCP Servers
        mcp_configs = self.load_mcp_server_configs()
        if not self.create_all_mcp_servers(mcp_configs):
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


class OpenFGAInitializer:
    """Handles OpenFGA authorization store initialization"""

    def __init__(self):
        self.store_id: Optional[str] = None
        self.authorization_model_id: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def log(self, message: str):
        """Print log message with prefix"""
        print(f"[OPENFGA-INIT] {message}", flush=True)

    def wait_for_fga_api(self) -> bool:
        """Wait for OpenFGA API to be ready"""
        self.log("Waiting for OpenFGA API to be ready...")
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.get(f"{FGA_BASE_URL}/stores", timeout=5)
                if response.status_code == 200:
                    self.log("OpenFGA API is ready!")
                    return True
            except requests.exceptions.RequestException as e:
                self.log(f"Attempt {attempt}/{MAX_RETRIES}: OpenFGA API not ready yet ({e})")
            
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        
        self.log("ERROR: OpenFGA API did not become ready in time")
        return False

    def get_or_create_store(self) -> bool:
        """Get existing store by name or create a new one"""
        self.log(f"Creating/finding store '{FGA_STORE_NAME}'...")
        
        # Check if store already exists
        try:
            response = self.session.get(
                f"{FGA_BASE_URL}/stores",
                params={"name": FGA_STORE_NAME},
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            stores = data.get("stores", [])
            
            for store in stores:
                if store.get("name") == FGA_STORE_NAME:
                    self.store_id = store.get("id")
                    self.log(f"✓ Found existing store with ID: {self.store_id}")
                    return True
        except requests.exceptions.RequestException as e:
            self.log(f"WARNING: Failed to check existing stores: {e}")
        
        # Create new store
        try:
            response = self.session.post(
                f"{FGA_BASE_URL}/stores",
                json={"name": FGA_STORE_NAME},
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            self.store_id = data.get("id")
            
            if not self.store_id:
                self.log("ERROR: No store ID in response")
                return False
            
            self.log(f"✓ Store created with ID: {self.store_id}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create store: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def parse_dsl_model(self, dsl_content: str) -> Dict[str, Any]:
        """Parse DSL model format into OpenFGA JSON format using native Python parser"""
        import re
        
        try:
            lines = dsl_content.strip().split('\n')
            schema_version = "1.1"
            type_definitions = []
            current_type = None
            current_relations = []
            
            for line in lines:
                stripped = line.strip()
                
                # Skip empty lines and model declaration
                if not stripped or stripped == 'model':
                    continue
                
                # Parse schema version
                if stripped.startswith('schema '):
                    schema_version = stripped.replace('schema ', '').strip()
                    continue
                
                # Parse type declaration
                if stripped.startswith('type '):
                    # Save previous type if exists
                    if current_type:
                        type_def = {"type": current_type}
                        if current_relations:
                            type_def["relations"] = {r["name"]: r["def"] for r in current_relations}
                            type_def["metadata"] = {
                                "relations": {r["name"]: r["metadata"] for r in current_relations if r.get("metadata")}
                            }
                        type_definitions.append(type_def)
                    
                    current_type = stripped.replace('type ', '').strip()
                    current_relations = []
                    continue
                
                # Parse relations section
                if stripped == 'relations':
                    continue
                
                # Parse define statement
                if stripped.startswith('define '):
                    relation_def = stripped.replace('define ', '').strip()
                    # Split on first colon to get relation name and definition
                    if ':' in relation_def:
                        rel_name, rel_value = relation_def.split(':', 1)
                        rel_name = rel_name.strip()
                        rel_value = rel_value.strip()
                        
                        parsed_relation = self._parse_relation_definition(rel_value)
                        current_relations.append({
                            "name": rel_name,
                            "def": parsed_relation["userset"],
                            "metadata": parsed_relation.get("metadata")
                        })
                    continue
            
            # Don't forget the last type
            if current_type:
                type_def = {"type": current_type}
                if current_relations:
                    type_def["relations"] = {r["name"]: r["def"] for r in current_relations}
                    # Only add metadata if there are directly_related_user_types
                    metadata_relations = {}
                    for r in current_relations:
                        if r.get("metadata"):
                            metadata_relations[r["name"]] = r["metadata"]
                    if metadata_relations:
                        type_def["metadata"] = {"relations": metadata_relations}
                type_definitions.append(type_def)
            
            return {
                "schema_version": schema_version,
                "type_definitions": type_definitions
            }
            
        except Exception as e:
            self.log(f"ERROR: Failed to parse DSL model: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def _parse_relation_definition(self, definition: str) -> Dict[str, Any]:
        """Parse a relation definition like '[user]', 'owner or admin from hotel', etc."""
        import re
        
        definition = definition.strip()
        result = {"userset": {}, "metadata": None}
        
        # Handle direct assignment like [user] or [user, hotel#admin]
        direct_match = re.match(r'^\[([^\]]+)\]$', definition)
        if direct_match:
            types_str = direct_match.group(1)
            directly_related = []
            for t in types_str.split(','):
                t = t.strip()
                if '#' in t:
                    type_name, relation = t.split('#', 1)
                    directly_related.append({"type": type_name.strip(), "relation": relation.strip()})
                else:
                    directly_related.append({"type": t})
            
            result["userset"] = {"this": {}}
            result["metadata"] = {"directly_related_user_types": directly_related}
            return result
        
        # Handle combined definitions with 'or'
        if ' or ' in definition:
            parts = definition.split(' or ')
            children = []
            all_directly_related = []
            
            for part in parts:
                part = part.strip()
                parsed = self._parse_single_relation(part)
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
        """Parse a single relation part like 'owner', 'admin from hotel', '[user]'"""
        import re
        
        part = part.strip()
        
        # Handle [type] or [type#relation]
        direct_match = re.match(r'^\[([^\]]+)\]$', part)
        if direct_match:
            types_str = direct_match.group(1)
            directly_related = []
            for t in types_str.split(','):
                t = t.strip()
                if '#' in t:
                    type_name, relation = t.split('#', 1)
                    directly_related.append({"type": type_name.strip(), "relation": relation.strip()})
                else:
                    directly_related.append({"type": t})
            return {"userset": {"this": {}}, "directly_related": directly_related}
        
        # Handle 'relation from tupleset_relation'
        from_match = re.match(r'^(\w+)\s+from\s+(\w+)$', part)
        if from_match:
            computed_rel = from_match.group(1)
            tupleset_rel = from_match.group(2)
            return {
                "userset": {
                    "tupleToUserset": {
                        "tupleset": {"relation": tupleset_rel},
                        "computedUserset": {"relation": computed_rel}
                    }
                }
            }
        
        # Simple computed userset (reference to another relation)
        return {"userset": {"computedUserset": {"relation": part}}}

    def load_config(self) -> Optional[Dict[str, Any]]:
        """Load configuration from openfgastore.yaml"""
        self.log(f"Loading configuration from {FGA_CONFIG_FILE}...")
        
        try:
            with open(FGA_CONFIG_FILE, 'r') as f:
                config = yaml.safe_load(f)
            self.log("✓ Configuration loaded successfully")
            return config
        except FileNotFoundError:
            self.log(f"ERROR: Configuration file not found: {FGA_CONFIG_FILE}")
            return None
        except yaml.YAMLError as e:
            self.log(f"ERROR: Failed to parse YAML: {e}")
            return None

    def create_authorization_model(self, model_dsl: str) -> bool:
        """Create authorization model from DSL, or use existing if identical"""
        self.log("Checking for existing authorization model...")
        
        try:
            model_json = self.parse_dsl_model(model_dsl)
            
            if not model_json.get("type_definitions"):
                self.log("ERROR: Failed to parse model DSL - no type definitions")
                return False
            
            # Check if an identical model already exists
            existing_model_id = self._find_existing_authorization_model(model_json)
            if existing_model_id:
                self.authorization_model_id = existing_model_id
                self.log(f"✓ Using existing authorization model with ID: {self.authorization_model_id}")
                return True
            
            # Create new model
            self.log("Creating new authorization model...")
            response = self.session.post(
                f"{FGA_BASE_URL}/stores/{self.store_id}/authorization-models",
                json=model_json,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            self.authorization_model_id = data.get("authorization_model_id")
            
            if not self.authorization_model_id:
                self.log("ERROR: No authorization_model_id in response")
                return False
            
            self.log(f"✓ Authorization model created with ID: {self.authorization_model_id}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create authorization model: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False
    
    def _find_existing_authorization_model(self, new_model: Dict[str, Any]) -> Optional[str]:
        """Check if an authorization model with identical type definitions already exists"""
        import json
        
        try:
            # Get all existing authorization models
            response = self.session.get(
                f"{FGA_BASE_URL}/stores/{self.store_id}/authorization-models",
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            existing_models = data.get("authorization_models", [])
            
            if not existing_models:
                return None
            
            # Normalize the new model for comparison
            new_types = self._normalize_type_definitions(new_model.get("type_definitions", []))
            new_types_json = json.dumps(new_types, sort_keys=True)
            
            # Compare with each existing model
            for existing in existing_models:
                existing_types = self._normalize_type_definitions(existing.get("type_definitions", []))
                existing_types_json = json.dumps(existing_types, sort_keys=True)
                
                if new_types_json == existing_types_json:
                    return existing.get("id")
            
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"WARNING: Failed to check existing models: {e}")
            return None
    
    def _normalize_type_definitions(self, type_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize type definitions by removing empty/null fields for comparison"""
        normalized = []
        for type_def in sorted(type_defs, key=lambda x: x.get("type", "")):
            norm_type = {"type": type_def.get("type")}
            
            # Normalize relations
            if type_def.get("relations"):
                norm_type["relations"] = {}
                for rel_name, rel_def in type_def["relations"].items():
                    norm_type["relations"][rel_name] = self._normalize_userset(rel_def)
            
            # Normalize metadata - extract just the directly_related_user_types
            if type_def.get("metadata") and type_def["metadata"].get("relations"):
                norm_metadata = {"relations": {}}
                for rel_name, rel_meta in type_def["metadata"]["relations"].items():
                    if rel_meta and rel_meta.get("directly_related_user_types"):
                        # Normalize each directly related user type
                        norm_drut = []
                        for drut in rel_meta["directly_related_user_types"]:
                            norm_item = {"type": drut.get("type")}
                            if drut.get("relation"):
                                norm_item["relation"] = drut["relation"]
                            norm_drut.append(norm_item)
                        if norm_drut:
                            norm_metadata["relations"][rel_name] = {
                                "directly_related_user_types": sorted(
                                    norm_drut,
                                    key=lambda x: (x.get("type", ""), x.get("relation", ""))
                                )
                            }
                if norm_metadata["relations"]:
                    norm_type["metadata"] = norm_metadata
            
            normalized.append(norm_type)
        return normalized
    
    def _normalize_userset(self, userset: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively normalize a userset definition, removing empty fields"""
        if not userset:
            return {}
        
        result = {}
        
        if "this" in userset:
            result["this"] = {}
        
        if "computedUserset" in userset:
            cu = userset["computedUserset"]
            result["computedUserset"] = {"relation": cu.get("relation", "")}
        
        if "tupleToUserset" in userset:
            ttu = userset["tupleToUserset"]
            result["tupleToUserset"] = {
                "tupleset": {"relation": ttu.get("tupleset", {}).get("relation", "")},
                "computedUserset": {"relation": ttu.get("computedUserset", {}).get("relation", "")}
            }
        
        if "union" in userset:
            children = []
            for child in userset["union"].get("child", []):
                children.append(self._normalize_userset(child))
            result["union"] = {"child": children}
        
        if "intersection" in userset:
            children = []
            for child in userset["intersection"].get("child", []):
                children.append(self._normalize_userset(child))
            result["intersection"] = {"child": children}
        
        if "difference" in userset:
            base = self._normalize_userset(userset["difference"].get("base", {}))
            subtract = self._normalize_userset(userset["difference"].get("subtract", {}))
            result["difference"] = {"base": base, "subtract": subtract}
        
        return result

    def write_tuples(self, tuples: List[Dict[str, str]]) -> bool:
        """Write relationship tuples to the store"""
        self.log(f"Writing {len(tuples)} relationship tuples...")
        
        if not tuples:
            self.log("No tuples to write")
            return True
        
        # Convert tuples to API format
        tuple_keys = []
        for t in tuples:
            tuple_keys.append({
                "user": t.get("user"),
                "relation": t.get("relation"),
                "object": t.get("object")
            })
        
        try:
            response = self.session.post(
                f"{FGA_BASE_URL}/stores/{self.store_id}/write",
                json={
                    "writes": {
                        "tuple_keys": tuple_keys,
                        "on_duplicate": "ignore"
                    },
                    "authorization_model_id": self.authorization_model_id
                },
                timeout=10
            )
            response.raise_for_status()
            
            self.log(f"✓ {len(tuples)} relationship tuples written successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to write tuples: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def run(self) -> bool:
        """Run the OpenFGA initialization process"""
        self.log("Starting OpenFGA authorization store initialization...")
        self.log("=" * 80)
        
        # Wait for OpenFGA API to be ready
        if not self.wait_for_fga_api():
            return False
        
        # Load configuration
        config = self.load_config()
        if not config:
            return False
        
        # Step 1: Create or get store
        if not self.get_or_create_store():
            return False
        
        # Step 2: Create authorization model
        model_dsl = config.get("model", "")
        if not model_dsl:
            self.log("ERROR: No model found in configuration")
            return False
        
        if not self.create_authorization_model(model_dsl):
            return False
        
        # Step 3: Write tuples
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


def main():
    """Main entry point"""
    # Step 1: Initialize Access Management
    am_initializer = GraviteeInitializer()
    
    try:
        am_success = am_initializer.run()
        if not am_success:
            sys.exit(1)
    except KeyboardInterrupt:
        am_initializer.log("Initialization interrupted by user")
        sys.exit(1)
    except Exception as e:
        am_initializer.log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Step 2: Initialize OpenFGA
    fga_initializer = OpenFGAInitializer()
    
    try:
        fga_success = fga_initializer.run()
        if not fga_success:
            sys.exit(1)
    except KeyboardInterrupt:
        fga_initializer.log("Initialization interrupted by user")
        sys.exit(1)
    except Exception as e:
        fga_initializer.log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Step 3: Create OpenFGA Authorization Engine in AM
    try:
        store_id = fga_initializer.store_id
        authorization_model_id = fga_initializer.authorization_model_id
        if store_id:
            am_initializer.log("=" * 80)
            am_initializer.log("Creating OpenFGA Authorization Engine in Access Management...")
            if not am_initializer.create_openfga_authorization_engine(store_id, authorization_model_id):
                sys.exit(1)
            am_initializer.log("✓ OpenFGA Authorization Engine configured in AM")
        else:
            am_initializer.log("WARNING: No OpenFGA store ID available, skipping authorization engine creation")
    except Exception as e:
        am_initializer.log(f"FATAL ERROR creating authorization engine: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("[INIT] ✓ All initialization completed successfully!", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
