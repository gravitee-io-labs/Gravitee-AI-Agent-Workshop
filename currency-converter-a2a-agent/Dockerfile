# Use a slim Python base
FROM python:3.13-slim

# Install uv for dependency management and running
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml main.py ./

# Pre-install all dependencies via uv (install deps directly instead of package)
RUN uv venv \
 && uv pip install --no-cache-dir \
    "a2a-sdk[http-server]>=0.3.22" \
    "httpx>=0.28.1" \
    "langchain-google-genai>=2.0.10" \
    "langgraph>=0.3.18" \
    "pydantic>=2.10.6" \
    "python-dotenv>=1.1.0" \
    "click>=8.1.8" \
    "uvicorn>=0.34.0"

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "10000"]
