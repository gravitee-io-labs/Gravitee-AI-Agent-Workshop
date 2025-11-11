#!/usr/bin/env python3
"""
Gravitee Initialization Orchestrator
This script orchestrates the initialization of both Access Management (AM) and API Management (APIM).
"""

import sys
import subprocess

def log(message: str):
    """Print log message with prefix"""
    print(f"[GRAVITEE-INIT] {message}", flush=True)

def main():
    """Main entry point"""
    log("=" * 80)
    log("Starting Gravitee Platform Initialization")
    log("=" * 80)
    
    # Step 1: Initialize Access Management
    log("")
    log("STEP 1: Initializing Access Management (AM)...")
    log("-" * 80)
    
    try:
        result = subprocess.run(
            [sys.executable, "/app/am_init.py"],
            check=True,
            capture_output=False
        )
        log("✓ Access Management initialization completed")
    except subprocess.CalledProcessError as e:
        log(f"✗ Access Management initialization failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        log(f"✗ Failed to run AM initialization: {e}")
        sys.exit(1)
    
    # Step 2: Initialize API Management
    log("")
    log("STEP 2: Initializing API Management (APIM)...")
    log("-" * 80)
    
    try:
        result = subprocess.run(
            [sys.executable, "/app/apim_init.py"],
            check=True,
            capture_output=False
        )
        log("✓ API Management initialization completed")
    except subprocess.CalledProcessError as e:
        log(f"✗ API Management initialization failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        log(f"✗ Failed to run APIM initialization: {e}")
        sys.exit(1)
    
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
