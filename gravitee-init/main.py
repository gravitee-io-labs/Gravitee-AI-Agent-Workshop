#!/usr/bin/env python3
"""
Gravitee Initialization Orchestrator
Waits for AM and APIM to be fully ready, then runs their initialization scripts.
"""

import os
import sys
import subprocess
import time

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AM_BASE_URL = os.getenv("AM_BASE_URL", "http://localhost:8093")
AM_USERNAME = os.getenv("AM_USERNAME", "admin")
AM_PASSWORD = os.getenv("AM_PASSWORD", "adminadmin")

APIM_BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8083")
APIM_USERNAME = os.getenv("APIM_USERNAME", "admin")
APIM_PASSWORD = os.getenv("APIM_PASSWORD", "admin")

ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")

MAX_RETRIES = 60
RETRY_DELAY = 5


def log(message: str):
    print(f"[GRAVITEE-INIT] {message}", flush=True)


# ---------------------------------------------------------------------------
# Readiness checks
# ---------------------------------------------------------------------------

def _wait_for_am() -> bool:
    """Wait until AM health check responds AND authentication succeeds."""
    health_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}"
    auth_url = f"{AM_BASE_URL}/management/auth/token"

    log("Waiting for Access Management to be fully ready...")
    for attempt in range(1, MAX_RETRIES + 1):
        # Phase 1 — health check
        try:
            r = requests.get(health_url, timeout=5)
            if r.status_code not in (200, 401):
                raise Exception(f"HTTP {r.status_code}")
        except Exception as exc:
            log(f"  AM attempt {attempt}/{MAX_RETRIES}: API not reachable ({exc})")
            time.sleep(RETRY_DELAY)
            continue

        # Phase 2 — authenticated call (token endpoint)
        try:
            r = requests.post(auth_url, auth=(AM_USERNAME, AM_PASSWORD), timeout=10)
            if r.status_code == 200 and r.json().get("access_token"):
                log("✓ Access Management is fully ready (health OK, auth OK)")
                return True
            raise Exception(f"HTTP {r.status_code}")
        except Exception as exc:
            log(f"  AM attempt {attempt}/{MAX_RETRIES}: API reachable but auth not ready yet ({exc})")
            time.sleep(RETRY_DELAY)

    log("ERROR: Access Management did not become ready in time")
    return False


def _wait_for_apim() -> bool:
    """Wait until APIM health check responds AND an authenticated API list succeeds."""
    health_url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}"
    apis_url = f"{health_url}/apis"

    log("Waiting for API Management to be fully ready...")
    for attempt in range(1, MAX_RETRIES + 1):
        # Phase 1 — health check
        try:
            r = requests.get(health_url, auth=(APIM_USERNAME, APIM_PASSWORD), timeout=5)
            if r.status_code not in (200, 401):
                raise Exception(f"HTTP {r.status_code}")
        except Exception as exc:
            log(f"  APIM attempt {attempt}/{MAX_RETRIES}: API not reachable ({exc})")
            time.sleep(RETRY_DELAY)
            continue

        # Phase 2 — authenticated call (list APIs)
        try:
            r = requests.get(apis_url, auth=(APIM_USERNAME, APIM_PASSWORD), timeout=10)
            if r.status_code == 200:
                log("✓ API Management is fully ready (health OK, API list OK)")
                return True
            raise Exception(f"HTTP {r.status_code}")
        except Exception as exc:
            log(f"  APIM attempt {attempt}/{MAX_RETRIES}: API reachable but not ready yet ({exc})")
            time.sleep(RETRY_DELAY)

    log("ERROR: API Management did not become ready in time")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("=" * 80)
    log("Starting Gravitee Platform Initialization")
    log("=" * 80)

    # Pre-flight: wait for both services to be fully operational
    log("")
    log("PRE-FLIGHT: Waiting for all services to be ready...")
    log("-" * 80)

    if not _wait_for_am():
        sys.exit(1)
    if not _wait_for_apim():
        sys.exit(1)

    log("")
    log("✓ All services are ready")
    log("")

    # Step 1: Initialize Access Management
    log("STEP 1: Initializing Access Management (AM)...")
    log("-" * 80)

    try:
        subprocess.run(
            [sys.executable, "/app/am_init.py"],
            check=True,
            capture_output=False,
        )
        log("✓ Access Management initialization completed")
    except subprocess.CalledProcessError as e:
        log(f"✗ Access Management initialization failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        log(f"✗ Failed to run AM initialization: {e}")
        sys.exit(1)

    # Step 2: Register AIM catalog source (Tavily MCP server)
    log("")
    log("STEP 2: Registering AIM catalog source (Tavily MCP)...")
    log("-" * 80)

    # gamma is served through apim-rest-api internally (same as $APIM_URL/gamma/...)
    gamma_url = os.getenv("GAMMA_BASE_URL", APIM_BASE_URL)
    aim_catalog_url = (
        f"{gamma_url}/gamma/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}"
        "/modules/aim/catalog/sources"
    )
    aim_payload = {
        "sourceKind": "mcp.server",
        "definition": {
            "type": "mcp-gateway",
            "name": "https://mcp.tavily.com/mcp/",
        },
    }
    try:
        r = requests.post(
            aim_catalog_url,
            json=aim_payload,
            auth=(APIM_USERNAME, APIM_PASSWORD),
            timeout=15,
        )
        if r.status_code in (200, 201):
            log(f"✓ AIM catalog source registered (HTTP {r.status_code})")
        elif r.status_code == 409:
            log("✓ AIM catalog source already exists (HTTP 409)")
        else:
            log(f"WARNING: AIM catalog source registration returned HTTP {r.status_code}: {r.text}")
    except Exception as e:
        log(f"WARNING: Failed to register AIM catalog source: {e}")

    # Step 3: Initialize API Management
    log("")
    log("STEP 3: Initializing API Management (APIM)...")
    log("-" * 80)

    try:
        subprocess.run(
            [sys.executable, "/app/apim_init.py"],
            check=True,
            capture_output=False,
        )
        log("✓ API Management initialization completed")
    except subprocess.CalledProcessError as e:
        log(f"✗ API Management initialization failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        log(f"✗ Failed to run APIM initialization: {e}")
        sys.exit(1)

    # Step 4: Initialize Gamma AIM catalog (MCP tools)
    log("")
    log("STEP 4: Initializing Gamma AIM catalog (MCP tools)...")
    log("-" * 80)

    try:
        subprocess.run(
            [sys.executable, "/app/gamma_init.py"],
            check=True,
            capture_output=False,
        )
        log("✓ Gamma AIM initialization completed")
    except subprocess.CalledProcessError as e:
        log(f"WARNING: Gamma AIM initialization failed with exit code {e.returncode}")
        # Non-fatal: AIM tools are best-effort
    except Exception as e:
        log(f"WARNING: Failed to run Gamma initialization: {e}")

    # All done
    log("")
    log("=" * 80)
    log("✓ Gravitee Platform Initialization Completed Successfully!")
    log("=" * 80)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Initialization interrupted by user")
        sys.exit(1)
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
