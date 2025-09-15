#!/bin/bash

echo "Starting Gravitee AI Workshop - Hotel Booking Demo"
echo "======================================================"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker and try again."
    exit 1
fi

echo "Building and starting all services..."
echo ""

# Build and start all services
docker compose up --build -d

echo ""
echo "Services starting up..."
echo ""

# Wait for services to be healthy
echo "Waiting for services to be ready..."

# Function to check if a service is healthy
check_service() {
    local service_name=$1
    local port=$2
    local path=${3:-""}
    local max_attempts=30
    local attempt=1

    echo "Checking $service_name..."
    
    while [ $attempt -le $max_attempts ]; do
        if curl -s -f "http://localhost:${port}${path}" > /dev/null 2>&1; then
            echo "✅ $service_name is ready!"
            return 0
        fi
        echo "⏳ Attempt $attempt/$max_attempts - $service_name not ready yet..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo "❌ $service_name failed to start within expected time"
    return 1
}

# Check Ollama
echo "Waiting for Ollama to be ready..."
sleep 5  # Give Ollama some time to start
if docker compose exec -T ollama ollama list | grep -q "qwen3:0.6b"; then
    echo "✅ Ollama with qwen3:0.6b model is ready!"
else
    echo "⏳ Waiting for Ollama model to download..."
    sleep 20
    if docker compose exec -T ollama ollama list | grep -q "qwen3:0.6b"; then
        echo "✅ Ollama with qwen3:0.6b model is ready!"
    else
        echo "❌ Ollama model download may have failed"
    fi
fi

# Check Hotel API
check_service "Hotel Booking API" 8000 "/docs"

# Check A2A Agent Server  
check_service "A2A Agent Server" 8080 "/health" || echo "⚠️  Agent Server might still be starting..."

echo ""
echo "🎉 All services are up and running!"
echo ""
echo "Available Services:"
echo "==================="
echo "🏨 Hotel Booking API:     http://localhost:8000"
echo "   - API Documentation:   http://localhost:8000/docs"
echo "   - MCP Endpoint:        http://localhost:8082/bookings/mcp"
echo ""
echo "🤖 A2A Agent Server:      http://localhost:8080"
echo "   - Health Check:        http://localhost:8080/health"
echo ""
echo "🧠 Ollama LLM:             http://localhost:11434"
echo "   - Model: qwen3:0.6b"
echo ""
echo "💬 MCP Client (Interactive): docker compose exec mcp-client mcp-client"
echo "🧠 LLM Client (Interactive): docker compose exec llm-client llm-client"
echo ""
echo "To test the A2A Agent Server, you can make requests to:"
echo "  POST http://localhost:8080/skills/hotel-booking-management"
echo ""
echo "To view logs: docker compose logs -f [service-name]"
echo "To stop all services: docker compose down"
echo ""
echo "Happy testing! 🚀"
