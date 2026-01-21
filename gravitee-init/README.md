# Gravitee Initialization Container

This initialization container automatically configures **Gravitee Access Management (AM)** and **API Management (APIM)** when the Docker Compose stack starts up.

## How it works

The container runs once after the Gravitee Management APIs (AM and APIM) are ready. It automatically configures both platforms with everything needed for the workshop:

- **Access Management**: creates the security domain, OAuth2 applications, test users, and configures OpenID Connect scopes
- **API Management**: imports API definitions, creates applications, and configures subscriptions

Configurations are defined in the following subfolders:
- `am-apps/` and `am-mcp-servers/` for Access Management
- `apim-apis/`, `apim-apps/` and `apim-subscriptions/` for API Management

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AM_BASE_URL` | AM Management API URL | `http://localhost:8093` |
| `APIM_BASE_URL` | APIM Management API URL | `http://localhost:8083` |
| `ORGANIZATION` | Gravitee organization | `DEFAULT` |
| `ENVIRONMENT` | Gravitee environment | `DEFAULT` |

## Logs

Logs are prefixed with `[GRAVITEE-INIT]` for easy tracking of the initialization process.
