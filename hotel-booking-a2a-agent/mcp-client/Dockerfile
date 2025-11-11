FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy the mcp client project
COPY . /app/mcp_client

WORKDIR /app/mcp_client

# Install the package
RUN pip install .

# Expose port if needed
EXPOSE 5000

# Default command starts the mcp client
CMD ["mcp-client"]
