#!/usr/bin/env python3
"""
Gravitee API Management (APIM) Initialization Script
This script imports API definitions into Gravitee API Management.
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from typing import Optional, List

# Configuration
APIM_BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8083")
APIM_USERNAME = os.getenv("APIM_USERNAME", "admin")
APIM_PASSWORD = os.getenv("APIM_PASSWORD", "admin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")
API_DEFINITIONS_DIR = os.getenv("API_DEFINITIONS_DIR", "/api-definitions")

MAX_RETRIES = 30
RETRY_DELAY = 5


class ApimInitializer:
    """Handles Gravitee API Management initialization"""

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = (APIM_USERNAME, APIM_PASSWORD)
        self.session.headers.update({"Content-Type": "application/json"})
        self.imported_apis: List[str] = []

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

    def import_api_definition(self, definition_file: Path) -> bool:
        """Import a single API definition"""
        self.log(f"Importing API definition from: {definition_file.name}")
        
        try:
            # Read the API definition file
            with open(definition_file, 'r') as f:
                api_definition = json.load(f)
            
            # Extract API name for logging
            api_name = api_definition.get("name", definition_file.stem)
            
            # Import the API
            url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/_import/definition"
            
            response = self.session.post(
                url,
                json=api_definition,
                timeout=30
            )
            
            # Check if API already exists
            if response.status_code == 400:
                error_data = response.json()
                error_message = str(error_data)
                if "already exists" in error_message.lower() or "duplicate" in error_message.lower():
                    self.log(f"✓ API '{api_name}' already exists, skipping import")
                    self.imported_apis.append(api_name)
                    return True
            
            response.raise_for_status()
            
            result = response.json()
            api_id = result.get("id", "unknown")
            
            self.log(f"✓ API '{api_name}' imported successfully (ID: {api_id})")
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

    def run(self) -> bool:
        """Run the APIM initialization process"""
        self.log("Starting Gravitee API Management initialization...")
        self.log("=" * 80)
        
        # Wait for APIM API to be ready
        if not self.wait_for_apim_api():
            return False
        
        # Get all API definition files
        definition_files = self.get_api_definition_files()
        
        if not definition_files:
            self.log("WARNING: No API definitions to import")
            return True
        
        # Import each API definition
        success_count = 0
        failure_count = 0
        
        for definition_file in definition_files:
            if self.import_api_definition(definition_file):
                success_count += 1
            else:
                failure_count += 1
        
        self.log("=" * 80)
        
        if failure_count == 0:
            self.log("✓ API Management initialization completed successfully!")
        else:
            self.log(f"⚠ API Management initialization completed with {failure_count} error(s)")
        
        self.log("")
        self.log("Summary:")
        self.log(f"  - Total API definitions: {len(definition_files)}")
        self.log(f"  - Successfully imported: {success_count}")
        self.log(f"  - Failed: {failure_count}")
        
        if self.imported_apis:
            self.log(f"  - Imported APIs: {', '.join(self.imported_apis)}")
        
        return failure_count == 0


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
