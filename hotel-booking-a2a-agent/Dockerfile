FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy the agent server project with integrated clients
COPY ./hotel-booking-a2a-agent /app

# Install the package
RUN pip install .

# Expose the agent server port
EXPOSE 8080

# Default command starts the agent server
CMD ["hotel-booking-a2a-agent"]
