FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py am_init.py apim_init.py openfgastore.yaml ./
COPY am-apps/ ./am-apps/
COPY am-mcp-servers/ ./am-mcp-servers/
COPY apim-apis/ ./apim-apis/
COPY apim-apps/ ./apim-apps/
COPY apim-subscriptions/ ./apim-subscriptions/

CMD ["python", "main.py"]
