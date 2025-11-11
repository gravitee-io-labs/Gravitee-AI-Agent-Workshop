# Gravitee Initialization Container

This initialization container automatically configures both Gravitee Access Management (AM) and API Management (APIM) when the Docker Compose stack starts up.

## Purpose

This container acts as an init container that runs once after the Gravitee Management APIs (both APIM and AM) are ready. It automatically configures both platforms with the base configuration needed for the workshop.

## Architecture

The initialization is split into three files:
- **`main.py`** - Orchestrator that runs both AM and APIM initialization sequentially
- **`am_init.py`** - Access Management (AM) initialization
- **`apim_init.py`** - API Management (APIM) initialization

## What It Does

### Access Management (AM) Initialization

The AM init performs the following steps in order:

1. **Wait for AM API** - Waits until the Access Management API is accessible
2. **Authenticate** - Gets an access token using basic auth (`admin:adminadmin`)
3. **Create Security Domain** - Creates a domain named "gravitee" with dataPlaneId "default"
4. **Enable Domain** - Enables the newly created domain (skipped if already enabled)
5. **Configure DCR Settings** - Sets Dynamic Client Registration to:
   - Allow localhost redirect URIs
   - Allow HTTP (unsecured) redirect URIs
6. **Create Application** - Creates a browser application with:
   - Name: "Gravitee Hotels"
   - Client ID: `gravitee-hotels`
   - Client Secret: `gravitee-hotels`
   - Redirect URIs: `http://localhost:8002/` and `https://oauth.pstmn.io/v1/callback`
7. **Add Scopes** - Adds OpenID Connect scopes: `openid`, `profile`, and `email`
8. **Add Identity Provider** - Associates the default system identity provider with the application
9. **Create User** - Creates a test user:
   - Username: `john.doe@gravitee.io`
   - Password: `HelloWorld@123`
   - Name: John Doe

### API Management (APIM) Initialization

The APIM init performs the following steps:

1. **Wait for APIM API** - Waits until the API Management API is accessible
2. **Import API Definitions** - Imports all API definitions from `/api-definitions` directory
   - Uses Basic Auth (`admin:admin`)
   - Imports each JSON file found in the directory
   - Skips APIs that already exist

## Log Format

All logs are prefixed with:
- `[GRAVITEE-INIT]` for the main orchestrator
- `[GRAVITEE-INIT]` for AM initialization
- `[GRAVITEE-INIT-APIM]` for APIM initialization

## Configuration

The container can be configured via environment variables:

### Access Management (AM)
- `AM_BASE_URL` - Base URL of the AM Management API (default: `http://localhost:8093`)
- `AM_USERNAME` - Admin username (default: `admin`)
- `AM_PASSWORD` - Admin password (default: `adminadmin`)

### API Management (APIM)
- `APIM_BASE_URL` - Base URL of the APIM Management API (default: `http://localhost:8083`)
- `APIM_USERNAME` - Admin username (default: `admin`)
- `APIM_PASSWORD` - Admin password (default: `admin`)
- `API_DEFINITIONS_DIR` - Directory containing API definition JSON files (default: `/api-definitions`)

### Common
- `ORGANIZATION` - Organization name (default: `DEFAULT`)
- `ENVIRONMENT` - Environment name (default: `DEFAULT`)

## Docker Compose Integration

The container is configured with:
- `restart: "no"` - Runs only once and doesn't restart
- Depends on both `am-management-api` and `apim-management-api` being healthy

## Files

- `Dockerfile` - Container definition using Python 3.11
- `requirements.txt` - Python dependencies (requests)
- `main.py` - Main orchestrator script
- `am_init.py` - Access Management initialization script
- `apim_init.py` - API Management initialization script
- `README.md` - This file

## Running Manually

If you need to run the initialization manually:

```bash
cd gravitee-init
docker build -t gravitee-init .
docker run --rm --network southbound \
  -v $(pwd)/../apim-apis-definitions:/api-definitions:ro \
  -e AM_BASE_URL=http://am-management-api:8093 \
  -e APIM_BASE_URL=http://apim-management-api:8083 \
  gravitee-init
```

To run only AM or APIM initialization:

```bash
# Only AM
docker run --rm --network southbound \
  -e AM_BASE_URL=http://am-management-api:8093 \
  gravitee-init python /app/am_init.py

# Only APIM
docker run --rm --network southbound \
  -v $(pwd)/../apim-apis-definitions:/api-definitions:ro \
  -e APIM_BASE_URL=http://apim-management-api:8083 \
  gravitee-init python /app/apim_init.py
```

## Error Handling

The script includes:
- Automatic retry logic for API availability
- Idempotent operations (handles existing resources)
- Clear error messages with response details
- Proper exit codes (0 for success, 1 for failure)

## Future Enhancements

Potential enhancements:
- Deploy/start imported APIs automatically
- Configure API plans and subscriptions
- Add additional users and roles
- Configure custom identity providers
- Set up API analytics and monitoring
