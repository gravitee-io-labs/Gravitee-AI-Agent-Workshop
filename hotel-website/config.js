// Default configuration for local development
// This file is overwritten in Docker with environment variables
window.APP_CONFIG = {
    agentCardUrl: 'http://localhost:8082/bookings-agent/.well-known/agent-card.json',
    oidcUrl: 'http://localhost:8092/gravitee/oidc/.well-known/openid-configuration',
    clientId: 'gravitee-hotels',
    clientSecret: 'gravitee-hotels',
    redirectUri: 'http://localhost:8002/',
    mcpServerResource: 'http://localhost:8082/hotels/mcp' // MCP Server resource URL (RFC 8707)
};
