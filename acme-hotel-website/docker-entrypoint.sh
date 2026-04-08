#!/bin/sh

# Generate config.js from environment variables
cat > /usr/share/nginx/html/config.js <<EOF
// Configuration generated from environment variables
window.APP_CONFIG = {
    agentCardUrl: '${AGENT_CARD_URL}',
    oidcUrl: '${OIDC_URL}',
    clientId: '${CLIENT_ID}',
    redirectUri: '${REDIRECT_URI}'
};
EOF

echo "Configuration generated:"
cat /usr/share/nginx/html/config.js

# Start nginx
exec nginx -g 'daemon off;'
