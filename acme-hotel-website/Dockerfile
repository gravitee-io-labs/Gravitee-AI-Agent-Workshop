FROM nginx:1.30-alpine

# The fixes Alpine published since the image was built (brotli-libs 1.2.1 for CVE-2026-111497)
RUN apk upgrade --no-cache

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy website files to nginx html directory
COPY index.html /usr/share/nginx/html/
COPY styles.css /usr/share/nginx/html/
COPY script.js /usr/share/nginx/html/
COPY assets /usr/share/nginx/html/assets

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose port 80
EXPOSE 80

# Use custom entrypoint to generate config
ENTRYPOINT ["/docker-entrypoint.sh"]
