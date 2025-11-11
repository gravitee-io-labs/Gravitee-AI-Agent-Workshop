FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy the llm client project
COPY . /app/llm_client

WORKDIR /app/llm_client

# Install the package
RUN pip install .

# Expose port if needed
EXPOSE 6000

# Default command starts the llm client
CMD ["llm-client"]
