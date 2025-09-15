FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install curl for healthchecks
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Copy the hotel API project
COPY . /app/hotel_booking_api

WORKDIR /app/hotel_booking_api

# Install the package
RUN pip install .

# Expose API port
EXPOSE 8000

# Default command starts the hotel API
CMD ["hotel-api"]