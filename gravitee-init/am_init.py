#!/usr/bin/env python3
"""
Gravitee Initialization Script
This script configures Gravitee Access Management (AM) with required settings.
"""

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
APP_SCOPES = ["openid", "profile", "email"]

# User configuration
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"
USER_EMAIL = "john.doe@gravitee.io"
USER_USERNAME = "john.doe@gravitee.io"
USER_PASSWORD = "HelloWorld@123"

MAX_RETRIES = 30
RETRY_DELAY = 5


class GraviteeInitializer:
    """Handles Gravitee Access Management initialization"""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.domain_id: Optional[str] = None
        self.app_id: Optional[str] = None
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
        
        # Step 5: Create application
        if not self.create_application():
            return False
        
        # Step 6: Add scopes to application
        if not self.add_scopes_to_application():
            return False
        
        # Step 7: Add identity provider to application
        if not self.add_identity_provider_to_application():
            return False
        
        # Step 8: Create user
        if not self.create_user():
            return False
        
        self.log("=" * 80)
        self.log("✓ Access Management initialization completed successfully!")
        self.log("")
        self.log("Summary:")
        self.log(f"  - Domain: {DOMAIN_NAME} (ID: {self.domain_id})")
        self.log(f"  - Application: {APP_NAME} (ID: {self.app_id})")
        self.log(f"  - Client ID: {APP_CLIENT_ID}")
        self.log(f"  - Client Secret: {APP_CLIENT_SECRET}")
        self.log(f"  - Redirect URIs: {', '.join(APP_REDIRECT_URIS)}")
        self.log(f"  - Scopes: {', '.join(APP_SCOPES)}")
        self.log(f"  - User: {USER_USERNAME}")
        
        return True


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
