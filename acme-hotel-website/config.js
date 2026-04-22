// Default configuration for local development
// This file is overwritten in Docker with environment variables
window.APP_CONFIG = {
    version: '2.0.0',
    agentCardUrl: 'http://localhost:8082/bookings-agent/.well-known/agent-card.json',
    oidcUrl: 'http://localhost:8092/gravitee/oidc/.well-known/openid-configuration',
    clientId: 'acme-hotels',
    redirectUri: 'http://localhost:8002/'
};
