FROM python:3.14

COPY requirements.txt .
COPY fastmcp-server.py .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# Create a non-root user and switch to it
RUN useradd -m appuser
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD [ "python", "fastmcp-server.py" ]
