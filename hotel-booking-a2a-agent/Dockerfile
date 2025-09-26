FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy the client packages first
COPY mcp-client /app/mcp-client
COPY llm-client /app/llm-client

# Copy the agent server project
COPY hotel-booking-a2a-agent /app/hotel-booking-a2a-agent

WORKDIR /app/hotel-booking-a2a-agent

# Install the package
RUN pip install .

# Expose the agent server port
EXPOSE 8080

# Default command starts the agent server
CMD ["hotel-booking-a2a-agent"]
