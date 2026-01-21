#!/usr/bin/env python3
"""
Gravitee API Management (APIM) Initialization Script
This script imports API definitions, creates Applications, and manages Subscriptions
in Gravitee API Management.
"""

import os
import sys
import json
import time
import requests
import yaml
from pathlib import Path
from typing import Optional, List, Dict, Any

# Configuration
APIM_BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8083")
APIM_USERNAME = os.getenv("APIM_USERNAME", "admin")
APIM_PASSWORD = os.getenv("APIM_PASSWORD", "admin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")
API_DEFINITIONS_DIR = os.getenv("API_DEFINITIONS_DIR", "/app/apim-apis")

# Directories for Applications and Subscriptions configurations
APIM_APPS_CONFIG_DIR = os.getenv("APIM_APPS_CONFIG_DIR", "/app/apim-apps")
APIM_SUBSCRIPTIONS_CONFIG_DIR = os.getenv("APIM_SUBSCRIPTIONS_CONFIG_DIR", "/app/apim-subscriptions")

MAX_RETRIES = 30
RETRY_DELAY = 5


class ApimInitializer:
    """Handles Gravitee API Management initialization"""

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = (APIM_USERNAME, APIM_PASSWORD)
        self.session.headers.update({"Content-Type": "application/json"})
        self.imported_apis: List[str] = []
        self.created_applications: List[str] = []
        self.created_subscriptions: List[str] = []

    def log(self, message: str):
        """Print log message with prefix"""
        print(f"[GRAVITEE-INIT-APIM] {message}", flush=True)

    def wait_for_apim_api(self) -> bool:
        """Wait for APIM Management API to be ready"""
        self.log("Waiting for API Management API to be ready...")
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}",
                    timeout=5
                )
                if response.status_code in [200, 401]:  # 401 means API is up but needs auth
                    self.log("API Management API is ready!")
                    return True
            except requests.exceptions.RequestException as e:
                self.log(f"Attempt {attempt}/{MAX_RETRIES}: APIM API not ready yet ({e})")
            
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        
        self.log("ERROR: API Management API did not become ready in time")
        return False

    def enable_next_gen_portal(self) -> bool:
        """Enable the next generation portal"""
        self.log("Enabling next generation portal...")
        
        try:
            url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/settings"
            
            # First, GET the current settings
            get_response = self.session.get(url, timeout=10)
            get_response.raise_for_status()
            
            settings = get_response.json()
            
            # Update the portalNext.access.enabled setting to true
            if "portalNext" not in settings:
                settings["portalNext"] = {}
            if "access" not in settings["portalNext"]:
                settings["portalNext"]["access"] = {}
            
            settings["portalNext"]["access"]["enabled"] = True
            
            # POST the updated settings back
            post_response = self.session.post(
                url,
                json=settings,
                timeout=10
            )
            
            post_response.raise_for_status()
            self.log("✓ Next generation portal enabled successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to enable next generation portal: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def enable_custom_api_key(self) -> bool:
        """Enable custom API key for plans"""
        self.log("Enabling custom API key...")
        
        try:
            url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/settings"
            
            # First, GET the current settings
            get_response = self.session.get(url, timeout=10)
            get_response.raise_for_status()
            
            settings = get_response.json()
            
            # Update the plan.security.customApiKey.enabled setting to true
            if "plan" not in settings:
                settings["plan"] = {}
            if "security" not in settings["plan"]:
                settings["plan"]["security"] = {}
            if "customApiKey" not in settings["plan"]["security"]:
                settings["plan"]["security"]["customApiKey"] = {}
            
            settings["plan"]["security"]["customApiKey"]["enabled"] = True
            
            # POST the updated settings back
            post_response = self.session.post(
                url,
                json=settings,
                timeout=10
            )
            
            post_response.raise_for_status()
            self.log("✓ Custom API key enabled successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to enable custom API key: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def update_portal_homepage(self) -> bool:
        """Update the Next Gen Dev Portal homepage to add /next prefix to links"""
        self.log("Updating Next Gen Dev Portal homepage...")
        
        try:
            # GET the homepage content
            get_url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/portal-pages?type=homepage&expands=content"
            
            get_response = self.session.get(get_url, timeout=10)
            get_response.raise_for_status()
            
            pages_data = get_response.json()
            pages = pages_data.get("pages", [])
            
            if not pages:
                self.log("WARNING: No homepage found")
                return False
            
            homepage = pages[0]
            page_id = homepage.get("id")
            content = homepage.get("content", "")
            
            self.log(f"Found homepage with ID: {page_id}")
            
            # Update all links to include /next prefix (only if not already present)
            # Replace link="/catalog" with link="/next/catalog", etc.
            # But skip if link already starts with "/next"
            import re
            updated_content = re.sub(r'link="(/(?!next/)([^"]+))"', r'link="/next/\2"', content)
            
            # Check if any changes were made
            if updated_content == content:
                self.log("✓ Homepage already has /next prefix in links")
                return True
            
            # PATCH the updated content
            patch_url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/portal-pages/{page_id}"
            
            patch_payload = {
                "id": page_id,
                "content": updated_content,
                "type": homepage.get("type"),
                "context": homepage.get("context"),
                "published": homepage.get("published")
            }
            
            patch_response = self.session.patch(
                patch_url,
                json=patch_payload,
                timeout=10
            )
            
            patch_response.raise_for_status()
            self.log("✓ Portal homepage updated successfully with /next prefix")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to update portal homepage: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def create_portal_menu_link(self) -> bool:
        """Create a portal menu link for App Creation"""
        self.log("Creating portal menu link for App Creation...")
        
        try:
            # GET existing menu links to check if it already exists
            get_url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/ui/portal-menu-links"
            
            get_response = self.session.get(get_url, timeout=10)
            get_response.raise_for_status()
            
            menu_data = get_response.json()
            existing_links = menu_data.get("data", [])
            
            # Check if a link with the same target already exists
            target_url = "http://localhost:8085/applications/creation"
            
            for link in existing_links:
                if link.get("target") == target_url:
                    self.log(f"✓ Portal menu link for App Creation already exists (ID: {link.get('id')})")
                    return True
            
            # Create the new menu link
            post_url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/ui/portal-menu-links"
            
            menu_link_payload = {
                "name": "Create App",
                "type": "EXTERNAL",
                "target": target_url,
                "visibility": "PUBLIC"
            }
            
            post_response = self.session.post(
                post_url,
                json=menu_link_payload,
                timeout=10
            )
            
            post_response.raise_for_status()
            result = post_response.json()
            self.log(f"✓ Portal menu link for App Creation created successfully (ID: {result.get('id')})")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create portal menu link: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def get_api_definition_files(self) -> List[Path]:
        """Get all API definition JSON files from the definitions directory"""
        self.log(f"Looking for API definitions in: {API_DEFINITIONS_DIR}")
        
        definitions_path = Path(API_DEFINITIONS_DIR)
        if not definitions_path.exists():
            self.log(f"ERROR: API definitions directory not found: {API_DEFINITIONS_DIR}")
            return []
        
        json_files = list(definitions_path.glob("*.json"))
        
        if not json_files:
            self.log(f"WARNING: No JSON files found in {API_DEFINITIONS_DIR}")
            return []
        
        self.log(f"Found {len(json_files)} API definition file(s)")
        return sorted(json_files)

    def get_api_id_by_listener_path(self, api_definition: dict, api_name: str) -> Optional[str]:
        """Get the API ID by searching for the listener path"""
        # Extract listener paths from the API definition (under "api" key)
        api_data = api_definition.get("api", {})
        listeners = api_data.get("listeners", [])
        if not listeners:
            self.log(f"No listeners found in API definition for '{api_name}'")
            return None
        
        # Get the first path from the first listener
        paths = listeners[0].get("paths", [])
        if not paths:
            self.log(f"No paths found in listener for '{api_name}'")
            return None
        
        search_path = paths[0].get("path", "")
        if not search_path:
            self.log(f"No path value found for '{api_name}'")
            return None
        
        self.log(f"Searching for existing API with listener path: '{search_path}'...")
        
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis"
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            apis_data = response.json()
            apis = apis_data.get("data", [])
            
            for api in apis:
                api_listeners = api.get("listeners", [])
                for listener in api_listeners:
                    api_paths = listener.get("paths", [])
                    for path_obj in api_paths:
                        if path_obj.get("path") == search_path:
                            api_id = api.get("id")
                            self.log(f"Found existing API with path '{search_path}' - ID: {api_id}")
                            return api_id
            
            self.log(f"No existing API found with listener path '{search_path}'")
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to search for API with path '{search_path}': {e}")
            return None

    def publish_api(self, api_id: str, api_name: str) -> bool:
        """Publish an API by setting its lifecycle state to PUBLISHED"""
        self.log(f"Publishing API '{api_name}' (ID: {api_id})...")
        
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"
            
            # First, fetch the current API configuration
            get_response = self.session.get(url, timeout=10)
            get_response.raise_for_status()
            
            api_config = get_response.json()
            
            # Update the lifecycle state to PUBLISHED
            api_config["lifecycleState"] = "PUBLISHED"
            
            # Send the full API configuration with updated lifecycle state
            put_response = self.session.put(
                url,
                json=api_config,
                timeout=10
            )
            
            put_response.raise_for_status()
            self.log(f"✓ API '{api_name}' published successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to publish API '{api_name}': {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def start_api(self, api_id: str, api_name: str) -> bool:
        """Start an API"""
        self.log(f"Starting API '{api_name}' (ID: {api_id})...")
        
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}/_start"
            
            response = self.session.post(
                url,
                timeout=10
            )
            
            # Check if API is already started
            if response.status_code == 400:
                error_data = response.json()
                error_message = str(error_data)
                if "already started" in error_message.lower():
                    self.log(f"✓ API '{api_name}' is already started")
                    return True
            
            response.raise_for_status()
            self.log(f"✓ API '{api_name}' started successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to start API '{api_name}': {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def import_api_definition(self, definition_file: Path) -> bool:
        """Import a single API definition, then publish and start it"""
        self.log(f"Importing API definition from: {definition_file.name}")
        
        try:
            # Read the API definition file
            with open(definition_file, 'r') as f:
                api_definition = json.load(f)
            
            # Extract API name for logging (from "api" object)
            api_data = api_definition.get("api", {})
            api_name = api_data.get("name", definition_file.stem)
            
            # Import the API
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/_import/definition"
            
            response = self.session.post(
                url,
                json=api_definition,
                timeout=30
            )
            
            api_id = None
            
            # Check if API already exists
            if response.status_code == 400:
                error_data = response.json()
                error_message = str(error_data)
                if "already exists" in error_message.lower() or "duplicate" in error_message.lower():
                    self.log(f"✓ API '{api_name}' already exists")
                    # Get the API ID for the existing API by listener path
                    api_id = self.get_api_id_by_listener_path(api_definition, api_name)
                    if not api_id:
                        self.log(f"ERROR: Could not find API ID for '{api_name}'")
                        return False
            else:
                response.raise_for_status()
                result = response.json()
                api_id = result.get("id", "unknown")
                self.log(f"✓ API '{api_name}' imported successfully (ID: {api_id})")
            
            # Publish the API
            if not self.publish_api(api_id, api_name):
                self.log(f"WARNING: Failed to publish API '{api_name}', but continuing...")
            
            # Start the API
            if not self.start_api(api_id, api_name):
                self.log(f"WARNING: Failed to start API '{api_name}', but continuing...")
            
            self.imported_apis.append(api_name)
            return True
            
        except json.JSONDecodeError as e:
            self.log(f"ERROR: Failed to parse JSON from {definition_file.name}: {e}")
            return False
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to import API from {definition_file.name}: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False
        except Exception as e:
            self.log(f"ERROR: Unexpected error importing {definition_file.name}: {e}")
            return False

    # ==================== Application Management ====================

    def get_application_config_files(self) -> List[Path]:
        """Get all application configuration YAML files"""
        self.log(f"Looking for application configurations in: {APIM_APPS_CONFIG_DIR}")
        
        config_path = Path(APIM_APPS_CONFIG_DIR)
        if not config_path.exists():
            self.log(f"WARNING: Applications config directory not found: {APIM_APPS_CONFIG_DIR}")
            return []
        
        yaml_files = list(config_path.glob("*.yaml")) + list(config_path.glob("*.yml"))
        
        if not yaml_files:
            self.log(f"WARNING: No YAML files found in {APIM_APPS_CONFIG_DIR}")
            return []
        
        self.log(f"Found {len(yaml_files)} application configuration file(s)")
        return sorted(yaml_files)

    def get_application_by_name(self, app_name: str) -> Optional[Dict[str, Any]]:
        """Get an application by its name"""
        try:
            url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/applications"
            
            response = self.session.get(url, params={"query": app_name}, timeout=10)
            response.raise_for_status()
            
            apps = response.json()
            for app in apps:
                if app.get("name") == app_name:
                    return app
            
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to search for application '{app_name}': {e}")
            return None

    def create_application(self, config_file: Path) -> Optional[str]:
        """Create an application from a YAML configuration file"""
        self.log(f"Processing application config from: {config_file.name}")
        
        try:
            # Read the YAML configuration
            with open(config_file, 'r') as f:
                app_config = yaml.safe_load(f)
            
            app_name = app_config.get("name")
            if not app_name:
                self.log(f"ERROR: No 'name' field in {config_file.name}")
                return None
            
            # Check if application already exists
            existing_app = self.get_application_by_name(app_name)
            if existing_app:
                app_id = existing_app.get("id")
                self.log(f"✓ Application '{app_name}' already exists (ID: {app_id})")
                
                # Check if we need to update the client_id
                settings = app_config.get("settings", {})
                app_settings = settings.get("app", {})
                client_id = app_settings.get("client_id") or app_settings.get("clientId")
                
                if client_id:
                    existing_settings = existing_app.get("settings", {}).get("app", {})
                    existing_client_id = existing_settings.get("client_id")
                    
                    if existing_client_id != client_id:
                        # Update the application with the client_id
                        self.log(f"  Updating application with client_id: {client_id}")
                        self.update_application_client_id(app_id, app_name, app_config)
                
                self.created_applications.append(app_name)
                return app_id
            
            # Prepare the payload for application creation
            # Using the Management API v1 for Applications
            url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/applications"
            
            settings = app_config.get("settings", {"app": {"type": "SIMPLE"}})
            
            payload = {
                "name": app_name,
                "description": app_config.get("description", ""),
                "settings": settings
            }
            
            response = self.session.post(
                url,
                json=payload,
                timeout=10
            )
            
            response.raise_for_status()
            result = response.json()
            app_id = result.get("id")
            
            self.log(f"✓ Application '{app_name}' created successfully (ID: {app_id})")
            self.created_applications.append(app_name)
            return app_id
            
        except yaml.YAMLError as e:
            self.log(f"ERROR: Failed to parse YAML from {config_file.name}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create application from {config_file.name}: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return None
        except Exception as e:
            self.log(f"ERROR: Unexpected error creating application from {config_file.name}: {e}")
            return None

    def update_application_client_id(self, app_id: str, app_name: str, app_config: Dict[str, Any]) -> bool:
        """Update an application to set/update the client_id"""
        try:
            url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/applications/{app_id}"
            
            # Get the settings from the config
            settings = app_config.get("settings", {"app": {"type": "SIMPLE"}})
            
            payload = {
                "name": app_name,
                "description": app_config.get("description", ""),
                "settings": settings
            }
            
            response = self.session.put(
                url,
                json=payload,
                timeout=10
            )
            
            response.raise_for_status()
            self.log(f"  ✓ Application '{app_name}' updated with client_id")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to update application '{app_name}': {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    # ==================== Subscription Management ====================

    def get_subscription_config_files(self) -> List[Path]:
        """Get all subscription configuration YAML files"""
        self.log(f"Looking for subscription configurations in: {APIM_SUBSCRIPTIONS_CONFIG_DIR}")
        
        config_path = Path(APIM_SUBSCRIPTIONS_CONFIG_DIR)
        if not config_path.exists():
            self.log(f"WARNING: Subscriptions config directory not found: {APIM_SUBSCRIPTIONS_CONFIG_DIR}")
            return []
        
        yaml_files = list(config_path.glob("*.yaml")) + list(config_path.glob("*.yml"))
        
        if not yaml_files:
            self.log(f"WARNING: No YAML files found in {APIM_SUBSCRIPTIONS_CONFIG_DIR}")
            return []
        
        self.log(f"Found {len(yaml_files)} subscription configuration file(s)")
        return sorted(yaml_files)

    def get_api_by_name(self, api_name: str) -> Optional[Dict[str, Any]]:
        """Get an API by its name"""
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis"
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            apis_data = response.json()
            apis = apis_data.get("data", [])
            
            for api in apis:
                if api.get("name") == api_name:
                    return api
            
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to search for API '{api_name}': {e}")
            return None

    def get_plan_by_name(self, api_id: str, plan_name: str) -> Optional[Dict[str, Any]]:
        """Get a plan by its name for a specific API"""
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}/plans"
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            plans_data = response.json()
            plans = plans_data.get("data", [])
            
            for plan in plans:
                if plan.get("name") == plan_name:
                    return plan
            
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to get plans for API '{api_id}': {e}")
            return None

    def check_subscription_exists(self, api_id: str, application_id: str, plan_id: str) -> bool:
        """Check if a subscription already exists"""
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}/subscriptions"
            
            response = self.session.get(
                url,
                params={
                    "applicationIds": application_id,
                    "planIds": plan_id,
                    "statuses": ["ACCEPTED", "PENDING", "PAUSED"]
                },
                timeout=10
            )
            response.raise_for_status()
            
            subscriptions_data = response.json()
            subscriptions = subscriptions_data.get("data", [])
            
            return len(subscriptions) > 0
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to check existing subscriptions: {e}")
            return False

    def create_subscription(self, api_id: str, application_id: str, plan_id: str, 
                          api_name: str, app_name: str, plan_name: str) -> bool:
        """Create a subscription for an application to an API plan"""
        self.log(f"Creating subscription: '{app_name}' -> '{api_name}' (Plan: '{plan_name}')...")
        
        # Check if subscription already exists
        if self.check_subscription_exists(api_id, application_id, plan_id):
            self.log(f"✓ Subscription already exists: '{app_name}' -> '{api_name}' (Plan: '{plan_name}')")
            self.created_subscriptions.append(f"{app_name} -> {api_name} ({plan_name})")
            return True
        
        try:
            # Create subscription using V2 API
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}/subscriptions"
            
            payload = {
                "applicationId": application_id,
                "planId": plan_id
            }
            
            response = self.session.post(
                url,
                json=payload,
                timeout=10
            )
            
            response.raise_for_status()
            result = response.json()
            subscription_id = result.get("id")
            subscription_status = result.get("status")
            
            self.log(f"✓ Subscription created (ID: {subscription_id}, Status: {subscription_status})")
            
            # If the subscription is pending (manual validation required), accept it
            if subscription_status == "PENDING":
                self.accept_subscription(api_id, subscription_id)
            
            self.created_subscriptions.append(f"{app_name} -> {api_name} ({plan_name})")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to create subscription: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def accept_subscription(self, api_id: str, subscription_id: str) -> bool:
        """Accept a pending subscription"""
        self.log(f"Accepting subscription '{subscription_id}'...")
        
        try:
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}/subscriptions/{subscription_id}/_accept"
            
            response = self.session.post(
                url,
                json={},
                timeout=10
            )
            
            response.raise_for_status()
            self.log(f"✓ Subscription accepted")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log(f"ERROR: Failed to accept subscription: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                self.log(f"Response: {e.response.text}")
            return False

    def process_subscriptions(self, config_file: Path) -> int:
        """Process subscriptions from a YAML configuration file"""
        self.log(f"Processing subscriptions from: {config_file.name}")
        
        success_count = 0
        
        try:
            # Read the YAML configuration
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
            
            subscriptions = config.get("subscriptions", [])
            
            if not subscriptions:
                self.log(f"WARNING: No subscriptions defined in {config_file.name}")
                return 0
            
            for sub in subscriptions:
                app_name = sub.get("application")
                api_name = sub.get("api")
                plan_name = sub.get("plan")
                
                if not all([app_name, api_name, plan_name]):
                    self.log(f"ERROR: Subscription missing required fields (application, api, plan)")
                    continue
                
                # Get application ID
                app = self.get_application_by_name(app_name)
                if not app:
                    self.log(f"ERROR: Application '{app_name}' not found")
                    continue
                application_id = app.get("id")
                
                # Get API ID
                api = self.get_api_by_name(api_name)
                if not api:
                    self.log(f"ERROR: API '{api_name}' not found")
                    continue
                api_id = api.get("id")
                
                # Get Plan ID
                plan = self.get_plan_by_name(api_id, plan_name)
                if not plan:
                    self.log(f"ERROR: Plan '{plan_name}' not found for API '{api_name}'")
                    continue
                plan_id = plan.get("id")
                
                # Create subscription
                if self.create_subscription(api_id, application_id, plan_id, api_name, app_name, plan_name):
                    success_count += 1
            
            return success_count
            
        except yaml.YAMLError as e:
            self.log(f"ERROR: Failed to parse YAML from {config_file.name}: {e}")
            return 0
        except Exception as e:
            self.log(f"ERROR: Unexpected error processing subscriptions from {config_file.name}: {e}")
            return 0

    def run(self) -> bool:
        """Run the APIM initialization process"""
        self.log("Starting Gravitee API Management initialization...")
        self.log("=" * 80)
        
        # Wait for APIM API to be ready
        if not self.wait_for_apim_api():
            return False
        
        # Enable next generation portal
        if not self.enable_next_gen_portal():
            self.log("WARNING: Failed to enable next generation portal, but continuing...")
        
        # Enable custom API key
        if not self.enable_custom_api_key():
            self.log("WARNING: Failed to enable custom API key, but continuing...")
        
        # Update portal homepage
        if not self.update_portal_homepage():
            self.log("WARNING: Failed to update portal homepage, but continuing...")
        
        # Create portal menu link for App Creation
        if not self.create_portal_menu_link():
            self.log("WARNING: Failed to create portal menu link, but continuing...")
        
        # Get all API definition files
        definition_files = self.get_api_definition_files()
        
        if not definition_files:
            self.log("WARNING: No API definitions to import")
        else:
            # Import each API definition
            api_success_count = 0
            api_failure_count = 0
            
            for definition_file in definition_files:
                if self.import_api_definition(definition_file):
                    api_success_count += 1
                else:
                    api_failure_count += 1
            
            self.log(f"API Import: {api_success_count} successful, {api_failure_count} failed")
        
        self.log("-" * 80)
        
        # ==================== Application Creation ====================
        self.log("Processing Applications...")
        app_config_files = self.get_application_config_files()
        
        app_success_count = 0
        app_failure_count = 0
        
        for config_file in app_config_files:
            if self.create_application(config_file):
                app_success_count += 1
            else:
                app_failure_count += 1
        
        if app_config_files:
            self.log(f"Applications: {app_success_count} successful, {app_failure_count} failed")
        
        self.log("-" * 80)
        
        # ==================== Subscription Creation ====================
        self.log("Processing Subscriptions...")
        sub_config_files = self.get_subscription_config_files()
        
        total_sub_success = 0
        
        for config_file in sub_config_files:
            success_count = self.process_subscriptions(config_file)
            total_sub_success += success_count
        
        if sub_config_files:
            self.log(f"Subscriptions: {total_sub_success} created/verified")
        
        self.log("=" * 80)
        
        # Final Summary
        total_failures = 0
        if definition_files:
            total_failures += len(definition_files) - len([f for f in definition_files if self.import_api_definition])
        
        self.log("")
        self.log("Summary:")
        self.log(f"  - APIs processed: {len(self.imported_apis)}")
        if self.imported_apis:
            self.log(f"    APIs: {', '.join(self.imported_apis)}")
        
        self.log(f"  - Applications processed: {len(self.created_applications)}")
        if self.created_applications:
            self.log(f"    Applications: {', '.join(self.created_applications)}")
        
        self.log(f"  - Subscriptions processed: {len(self.created_subscriptions)}")
        if self.created_subscriptions:
            for sub in self.created_subscriptions:
                self.log(f"    - {sub}")
        
        self.log("")
        self.log("✓ API Management initialization completed!")
        
        return True


def main():
    """Main entry point"""
    initializer = ApimInitializer()
    
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
