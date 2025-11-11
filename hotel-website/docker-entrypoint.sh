#!/bin/sh

# Generate config.js from environment variables
cat > /usr/share/nginx/html/config.js <<EOF
// Configuration generated from environment variables
window.APP_CONFIG = {
    agentCardUrl: '${AGENT_CARD_URL:-http://localhost:8082/bookings-agent/.well-known/agent-card.json}',
    oidcUrl: '${OIDC_URL:-http://localhost:8092/gravitee/oidc/.well-known/openid-configuration}',
    clientId: '${CLIENT_ID:-gravitee-hotels}',
    clientSecret: '${CLIENT_SECRET:-gravitee-hotels}',
    redirectUri: '${REDIRECT_URI:-http://localhost:8002/}'
};
EOF

echo "Configuration generated:"
cat /usr/share/nginx/html/config.js

# Start nginx
exec nginx -g 'daemon off;'
