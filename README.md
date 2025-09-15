# Hotel Booking A2A Agent

A simple A2A (Agent-to-Agent) compliant hotel booking agent using MCP tools and local LLM.

## 🚀 Quick Start

### 1. Run Everything
```bash
docker compose up
```

Wait for all services to start (takes ~2-3 minutes for model download).

### 2. Get Agent Info
```bash
# Check agent card
curl http://localhost:8080/.well-known/agent-card.json
```

### 3. Use the Agent

#### List all bookings:
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "1",
        "role": "user",
        "parts": [{"text": "List all bookings"}]
      }
    }
  }'
```

#### Get booking details:
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2", 
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "2",
        "role": "user",
        "parts": [{"text": "Get booking details for ID 1"}]
      }
    }
  }'
```

#### Create a booking:
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "3",
    "method": "message/send", 
    "params": {
      "message": {
        "messageId": "3",
        "role": "user",
        "parts": [{"text": "Create a booking for John at Hotel Paris from 2025-03-01 to 2025-03-03"}]
      }
    }
  }'
```

## 🔧 Services

- **A2A Agent**: http://localhost:8080 (A2A protocol compliant)
- **Hotel API**: http://localhost:8000 (Backend with MCP tools)
- **Ollama LLM**: http://localhost:11434 (Local AI model)

## 🛑 Stop Everything

```bash
docker compose down
```

That's it! 🎉
