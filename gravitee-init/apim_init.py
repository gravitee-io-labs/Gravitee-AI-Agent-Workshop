#!/usr/bin/env python3
"""
Gravitee API Management (APIM) Initialization Script.
Imports API definitions, creates applications, and manages subscriptions.
"""

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APIM_BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8083")
APIM_USERNAME = os.getenv("APIM_USERNAME", "admin")
APIM_PASSWORD = os.getenv("APIM_PASSWORD", "admin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")
API_DEFINITIONS_DIR = os.getenv("API_DEFINITIONS_DIR", "/app/apim-apis")
APIM_APPS_CONFIG_DIR = os.getenv("APIM_APPS_CONFIG_DIR", "/app/apim-apps")
APIM_SUBSCRIPTIONS_CONFIG_DIR = os.getenv("APIM_SUBSCRIPTIONS_CONFIG_DIR", "/app/apim-subscriptions")

MAX_RETRIES = 30
RETRY_DELAY = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _list_yaml_files(directory: str) -> List[Path]:
    """Return sorted YAML files from a directory."""
    p = Path(directory)
    if not p.exists():
        return []
    return sorted(list(p.glob("*.yaml")) + list(p.glob("*.yml")))


# ───────────────────────────────────────────────────────────────────────────
# APIM Initializer
# ───────────────────────────────────────────────────────────────────────────

class ApimInitializer:
    """Handles Gravitee API Management initialization."""

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = (APIM_USERNAME, APIM_PASSWORD)
        self.session.headers.update({"Content-Type": "application/json"})
        self.imported_apis: List[str] = []
        self.created_applications: List[str] = []
        self.created_subscriptions: List[str] = []

    # -- Logging & URL helpers ---------------------------------------------

    def log(self, message: str):
        print(f"[GRAVITEE-INIT-APIM] {message}", flush=True)

    def _log_response_error(self, label: str, exc: requests.exceptions.RequestException):
        self.log(f"ERROR: {label}: {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "text"):
            self.log(f"  Response: {resp.text}")

    @property
    def _v1_url(self) -> str:
        return f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}"

    @property
    def _v2_url(self) -> str:
        return f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}"

    # -- Readiness ---------------------------------------------------------

    def wait_for_apim_api(self) -> bool:
        self.log("Waiting for API Management API to be ready...")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if self.session.get(f"{self._v1_url}", timeout=5).status_code in (200, 401):
                    self.log("API Management API is ready!")
                    return True
            except requests.exceptions.RequestException as exc:
                self.log(f"  Attempt {attempt}/{MAX_RETRIES}: not ready yet ({exc})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        self.log("ERROR: API Management API did not become ready in time")
        return False

    # -- Environment settings (single GET + POST) --------------------------

    def _configure_settings(self) -> bool:
        """Enable next-gen portal and custom API key in a single round-trip."""
        self.log("Configuring environment settings (next-gen portal, custom API key)...")
        try:
            url = f"{self._v1_url}/settings"
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            settings = r.json()

            # Next-gen portal
            settings.setdefault("portalNext", {}).setdefault("access", {})["enabled"] = True
            # Custom API key
            settings.setdefault("plan", {}).setdefault("security", {}).setdefault("customApiKey", {})["enabled"] = True

            r2 = self.session.post(url, json=settings, timeout=10)
            r2.raise_for_status()
            self.log("✓ Next generation portal enabled")
            self.log("✓ Custom API key enabled")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to configure settings", exc)
            return False

    # -- Portal homepage & menu link ---------------------------------------

    def _update_portal_homepage(self) -> bool:
        """Add /next prefix to portal homepage links if needed."""
        self.log("Updating Next Gen Dev Portal homepage...")
        try:
            r = self.session.get(
                f"{self._v2_url}/portal-pages",
                params={"type": "homepage", "expands": "content"},
                timeout=10,
            )
            r.raise_for_status()
            pages = r.json().get("pages", [])
            if not pages:
                self.log("WARNING: No homepage found")
                return False

            homepage = pages[0]
            page_id = homepage["id"]
            content = homepage.get("content", "")
            updated = re.sub(r'link="(/(?!next/)([^"]+))"', r'link="/next/\2"', content)

            if updated == content:
                self.log("✓ Homepage already has /next prefix in links")
                return True

            r2 = self.session.patch(
                f"{self._v2_url}/portal-pages/{page_id}",
                json={
                    "id": page_id,
                    "content": updated,
                    "type": homepage.get("type"),
                    "context": homepage.get("context"),
                    "published": homepage.get("published"),
                },
                timeout=10,
            )
            r2.raise_for_status()
            self.log("✓ Portal homepage updated with /next prefix")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to update portal homepage", exc)
            return False

    def _create_portal_menu_link(self) -> bool:
        """Create an external menu link for App Creation (idempotent)."""
        self.log("Creating portal menu link for App Creation...")
        target_url = "http://localhost:8085/applications/creation"
        try:
            url = f"{self._v2_url}/ui/portal-menu-links"
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            for link in r.json().get("data", []):
                if link.get("target") == target_url:
                    self.log(f"✓ Portal menu link already exists (ID: {link['id']})")
                    return True

            r2 = self.session.post(url, json={
                "name": "Create App",
                "type": "EXTERNAL",
                "target": target_url,
                "visibility": "PUBLIC",
            }, timeout=10)
            r2.raise_for_status()
            self.log(f"✓ Portal menu link created (ID: {r2.json().get('id')})")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create portal menu link", exc)
            return False

    # -- API import, publish, start ----------------------------------------

    def _get_api_id_by_listener_path(self, api_definition: dict, api_name: str) -> Optional[str]:
        """Resolve an existing API ID by matching listener path."""
        paths = (
            api_definition.get("api", {})
            .get("listeners", [{}])[0]
            .get("paths", [{}])
        )
        search_path = paths[0].get("path", "") if paths else ""
        if not search_path:
            self.log(f"No listener path found for '{api_name}'")
            return None

        self.log(f"Searching for existing API with listener path: '{search_path}'...")
        try:
            r = self.session.get(f"{self._v2_url}/apis", timeout=10)
            r.raise_for_status()
            for api in r.json().get("data", []):
                for listener in api.get("listeners", []):
                    for path_obj in listener.get("paths", []):
                        if path_obj.get("path") == search_path:
                            self.log(f"Found existing API with path '{search_path}' - ID: {api['id']}")
                            return api["id"]
            self.log(f"No existing API found with listener path '{search_path}'")
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error(f"Failed to search API by path '{search_path}'", exc)
            return None

    def _publish_api(self, api_id: str, api_name: str) -> bool:
        self.log(f"Publishing API '{api_name}' (ID: {api_id})...")
        try:
            url = f"{self._v2_url}/apis/{api_id}"
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            config = r.json()
            config["lifecycleState"] = "PUBLISHED"
            r2 = self.session.put(url, json=config, timeout=10)
            r2.raise_for_status()
            self.log(f"✓ API '{api_name}' published successfully")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error(f"Failed to publish API '{api_name}'", exc)
            return False

    def _start_api(self, api_id: str, api_name: str) -> bool:
        self.log(f"Starting API '{api_name}' (ID: {api_id})...")
        try:
            r = self.session.post(f"{self._v2_url}/apis/{api_id}/_start", timeout=10)
            if r.status_code == 400 and "already started" in r.text.lower():
                self.log(f"✓ API '{api_name}' is already started")
                return True
            r.raise_for_status()
            self.log(f"✓ API '{api_name}' started successfully")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error(f"Failed to start API '{api_name}'", exc)
            return False

    def _import_api_definition(self, definition_file: Path) -> bool:
        """Import a single API definition, then publish and start it."""
        self.log(f"Importing API definition from: {definition_file.name}")
        try:
            with open(definition_file, "r") as fh:
                api_definition = json.load(fh)

            api_name = api_definition.get("api", {}).get("name", definition_file.stem)

            r = self.session.post(
                f"{self._v2_url}/apis/_import/definition",
                json=api_definition,
                timeout=30,
            )

            if r.status_code == 400 and ("already exists" in r.text.lower() or "duplicate" in r.text.lower()):
                self.log(f"✓ API '{api_name}' already exists")
                api_id = self._get_api_id_by_listener_path(api_definition, api_name)
                if not api_id:
                    self.log(f"ERROR: Could not find API ID for '{api_name}'")
                    return False
            else:
                r.raise_for_status()
                api_id = r.json().get("id", "unknown")
                self.log(f"✓ API '{api_name}' imported successfully (ID: {api_id})")

            # Best-effort publish & start
            if not self._publish_api(api_id, api_name):
                self.log(f"WARNING: Failed to publish API '{api_name}', continuing...")
            if not self._start_api(api_id, api_name):
                self.log(f"WARNING: Failed to start API '{api_name}', continuing...")

            self.imported_apis.append(api_name)
            return True
        except json.JSONDecodeError as exc:
            self.log(f"ERROR: Invalid JSON in {definition_file.name}: {exc}")
            return False
        except requests.exceptions.RequestException as exc:
            self._log_response_error(f"Failed to import {definition_file.name}", exc)
            return False

    # -- Application management --------------------------------------------

    def _get_application_by_name(self, app_name: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.session.get(
                f"{self._v1_url}/applications",
                params={"query": app_name},
                timeout=10,
            )
            r.raise_for_status()
            for app in r.json():
                if app.get("name") == app_name:
                    return app
            return None
        except requests.exceptions.RequestException:
            return None

    def _create_application(self, config_file: Path) -> Optional[str]:
        """Create or update an APIM application from a YAML config file."""
        self.log(f"Processing application config from: {config_file.name}")
        try:
            with open(config_file, "r") as fh:
                app_config = yaml.safe_load(fh)

            app_name = app_config.get("name")
            if not app_name:
                self.log(f"ERROR: No 'name' field in {config_file.name}")
                return None

            settings = app_config.get("settings", {"app": {"type": "SIMPLE"}})
            payload = {"name": app_name, "description": app_config.get("description", ""), "settings": settings}

            existing = self._get_application_by_name(app_name)
            if existing:
                app_id = existing["id"]
                self.log(f"✓ Application '{app_name}' already exists (ID: {app_id})")

                # Sync client_id if needed
                desired_cid = settings.get("app", {}).get("client_id") or settings.get("app", {}).get("clientId")
                current_cid = existing.get("settings", {}).get("app", {}).get("client_id")
                if desired_cid and desired_cid != current_cid:
                    self.log(f"  Updating client_id to: {desired_cid}")
                    try:
                        self.session.put(f"{self._v1_url}/applications/{app_id}", json=payload, timeout=10).raise_for_status()
                        self.log(f"  ✓ Application '{app_name}' updated")
                    except requests.exceptions.RequestException as exc:
                        self._log_response_error(f"Failed to update '{app_name}'", exc)

                self.created_applications.append(app_name)
                return app_id

            r = self.session.post(f"{self._v1_url}/applications", json=payload, timeout=10)
            r.raise_for_status()
            app_id = r.json().get("id")
            self.log(f"✓ Application '{app_name}' created (ID: {app_id})")
            self.created_applications.append(app_name)
            return app_id
        except yaml.YAMLError as exc:
            self.log(f"ERROR: Invalid YAML in {config_file.name}: {exc}")
            return None
        except requests.exceptions.RequestException as exc:
            self._log_response_error(f"Failed to create application from {config_file.name}", exc)
            return None

    # -- Subscription management -------------------------------------------

    def _get_api_by_name(self, api_name: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.session.get(f"{self._v2_url}/apis", timeout=10)
            r.raise_for_status()
            for api in r.json().get("data", []):
                if api.get("name") == api_name:
                    return api
            return None
        except requests.exceptions.RequestException:
            return None

    def _get_plan_by_name(self, api_id: str, plan_name: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.session.get(f"{self._v2_url}/apis/{api_id}/plans", timeout=10)
            r.raise_for_status()
            for plan in r.json().get("data", []):
                if plan.get("name") == plan_name:
                    return plan
            return None
        except requests.exceptions.RequestException:
            return None

    def _subscription_exists(self, api_id: str, application_id: str, plan_id: str) -> bool:
        try:
            r = self.session.get(
                f"{self._v2_url}/apis/{api_id}/subscriptions",
                params={"applicationIds": application_id, "planIds": plan_id, "statuses": ["ACCEPTED", "PENDING", "PAUSED"]},
                timeout=10,
            )
            r.raise_for_status()
            return len(r.json().get("data", [])) > 0
        except requests.exceptions.RequestException:
            return False

    def _create_subscription(self, api_id: str, app_id: str, plan_id: str,
                             api_name: str, app_name: str, plan_name: str) -> bool:
        label = f"'{app_name}' -> '{api_name}' (Plan: '{plan_name}')"
        self.log(f"Creating subscription: {label}...")

        if self._subscription_exists(api_id, app_id, plan_id):
            self.log(f"✓ Subscription already exists: {label}")
            self.created_subscriptions.append(f"{app_name} -> {api_name} ({plan_name})")
            return True

        try:
            r = self.session.post(
                f"{self._v2_url}/apis/{api_id}/subscriptions",
                json={"applicationId": app_id, "planId": plan_id},
                timeout=10,
            )
            r.raise_for_status()
            result = r.json()
            sub_id = result.get("id")
            status = result.get("status")
            self.log(f"✓ Subscription created (ID: {sub_id}, Status: {status})")

            if status == "PENDING":
                self._accept_subscription(api_id, sub_id)

            self.created_subscriptions.append(f"{app_name} -> {api_name} ({plan_name})")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to create subscription", exc)
            return False

    def _accept_subscription(self, api_id: str, subscription_id: str) -> bool:
        self.log(f"Accepting subscription '{subscription_id}'...")
        try:
            r = self.session.post(
                f"{self._v2_url}/apis/{api_id}/subscriptions/{subscription_id}/_accept",
                json={},
                timeout=10,
            )
            r.raise_for_status()
            self.log("✓ Subscription accepted")
            return True
        except requests.exceptions.RequestException as exc:
            self._log_response_error("Failed to accept subscription", exc)
            return False

    def _process_subscriptions(self, config_file: Path) -> int:
        self.log(f"Processing subscriptions from: {config_file.name}")
        count = 0
        try:
            with open(config_file, "r") as fh:
                config = yaml.safe_load(fh)

            for sub in config.get("subscriptions", []):
                app_name, api_name, plan_name = sub.get("application"), sub.get("api"), sub.get("plan")
                if not all([app_name, api_name, plan_name]):
                    self.log("ERROR: Subscription missing required fields (application, api, plan)")
                    continue

                app = self._get_application_by_name(app_name)
                if not app:
                    self.log(f"ERROR: Application '{app_name}' not found")
                    continue
                api = self._get_api_by_name(api_name)
                if not api:
                    self.log(f"ERROR: API '{api_name}' not found")
                    continue
                plan = self._get_plan_by_name(api["id"], plan_name)
                if not plan:
                    self.log(f"ERROR: Plan '{plan_name}' not found for API '{api_name}'")
                    continue

                if self._create_subscription(api["id"], app["id"], plan["id"], api_name, app_name, plan_name):
                    count += 1
            return count
        except yaml.YAMLError as exc:
            self.log(f"ERROR: Invalid YAML in {config_file.name}: {exc}")
            return 0

    # -- Orchestration -----------------------------------------------------

    def run(self) -> bool:
        self.log("Starting Gravitee API Management initialization...")
        self.log("=" * 80)

        if not self.wait_for_apim_api():
            return False

        # Environment settings (best-effort)
        if not self._configure_settings():
            self.log("WARNING: Failed to configure settings, continuing...")

        # Portal customization (best-effort)
        if not self._update_portal_homepage():
            self.log("WARNING: Failed to update portal homepage, but continuing...")
        if not self._create_portal_menu_link():
            self.log("WARNING: Failed to create portal menu link, but continuing...")

        # --- APIs ---
        api_files = sorted(Path(API_DEFINITIONS_DIR).glob("*.json")) if Path(API_DEFINITIONS_DIR).exists() else []
        if api_files:
            self.log(f"Found {len(api_files)} API definition file(s)")
            ok = sum(1 for f in api_files if self._import_api_definition(f))
            self.log(f"API Import: {ok} successful, {len(api_files) - ok} failed")
        else:
            self.log("WARNING: No API definitions to import")

        self.log("-" * 80)

        # --- Applications ---
        self.log("Processing Applications...")
        app_files = _list_yaml_files(APIM_APPS_CONFIG_DIR)
        if app_files:
            self.log(f"Found {len(app_files)} application configuration file(s)")
            ok = sum(1 for f in app_files if self._create_application(f))
            self.log(f"Applications: {ok} successful, {len(app_files) - ok} failed")

        self.log("-" * 80)

        # --- Subscriptions ---
        self.log("Processing Subscriptions...")
        sub_files = _list_yaml_files(APIM_SUBSCRIPTIONS_CONFIG_DIR)
        if sub_files:
            self.log(f"Found {len(sub_files)} subscription configuration file(s)")
            total = sum(self._process_subscriptions(f) for f in sub_files)
            self.log(f"Subscriptions: {total} created/verified")

        # --- Summary ---
        self.log("=" * 80)
        self.log("")
        self.log("Summary:")
        self.log(f"  - APIs processed: {len(self.imported_apis)}")
        if self.imported_apis:
            self.log(f"    APIs: {', '.join(self.imported_apis)}")
        self.log(f"  - Applications processed: {len(self.created_applications)}")
        if self.created_applications:
            self.log(f"    Applications: {', '.join(self.created_applications)}")
        self.log(f"  - Subscriptions processed: {len(self.created_subscriptions)}")
        for sub in self.created_subscriptions:
            self.log(f"    - {sub}")
        self.log("")
        self.log("✓ API Management initialization completed!")
        return True


def main():
    initializer = ApimInitializer()
    try:
        sys.exit(0 if initializer.run() else 1)
    except KeyboardInterrupt:
        initializer.log("Initialization interrupted by user")
        sys.exit(1)
    except Exception as exc:
        initializer.log(f"FATAL ERROR: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
