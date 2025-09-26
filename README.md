# Gravitee AI Workshop

A hands-on workshop to explore **Gravitee Agent Mesh Features** with AI Agents, LLMs, and secure API management using visual inspectors and development tools.

## 🎯 What You'll Learn

Deploy and experiment with:

- **🤖 AI Agent Management**: Secure LLM/Agent exposure with policies  
- **🔐 Token Tracking & Guard Rails**: New Gravitee AI security policies
- **📋 Agent Discovery**: Unified agent catalog via A2A Agent Cards
- **🔧 MCP Tools Server**: Make your APIs discoverable as Tools to AI Agents via embedded MCP servers
- **🕵️ Interactive Testing**: Use visual inspectors and tools instead of command-line testing

## 🚀 Quick Start

### 1. Run the Workshop
```bash
docker compose up
```

Wait 2-3 minutes for all services to start and the Ollama model to download.

### 2. Access the Platform

| Service | URL | Description |
|---------|-----|-------------|
| **Gravitee Console** | http://localhost:8084 | API Management Console |
| **Gravitee Portal** | http://localhost:8085 | Developer Portal |
| **A2A Agent** | http://localhost:8080 | AI Agent (A2A Protocol) |
| **Hotel API** | http://localhost:8000 | Demo API with MCP tools |
| **MCP Inspector** | http://localhost:6274 | Visual MCP Protocol Inspector |
| **A2A Inspector** | http://localhost:8005 | Visual A2A Protocol Inspector |
| **Ollama LLM** | http://localhost:11434 | Local LLM Runtime |

### 3. Explore and Test

> **💡 Pro Tip**: Notice how both inspectors use `apim-gateway:8082` URLs instead of direct service URLs. This demonstrates how all AI agent interactions flow through the Gravitee API Gateway for security, monitoring, and policy enforcement!

#### 🕵️ **A2A Inspector** - Agent Protocol Testing
Visit http://localhost:8005 to:
1. **Configure Agent Card URL**: 
   ```
   http://apim-gateway:8082/agents/bookings/v1/.well-known/agent-card.json
   ```
2. **Explore Agent Capabilities**: View agent metadata and available skills
3. **Interactive Chat**: Test conversations with examples like:
   - *"List the hotel bookings options please"*
   - *"Create a booking for John at Hotel Paris"*
   - *"Show me booking details for ID 1"*
4. **Protocol Debugging**: Inspect A2A protocol messages in real-time

![A2A Inspector Interface](./assets/a2a-inspector.png)

#### 🔧 **MCP Inspector** - Tool Protocol Testing  
Visit http://localhost:6274 to:
1. **Select Protocol**: Choose **"Streamable HTTP"**
2. **Configure MCP Server URL**: 
   ```
   http://apim-gateway:8082/bookings/mcp
   ```
3. **List Tools**: Discover available booking tools (list, get, create, update, delete)
4. **Call Tools**: Execute tools interactively to:
   - Get all bookings
   - Create new bookings
   - Update existing bookings
   - Delete bookings
5. **Protocol Analysis**: Debug MCP tool discovery and execution flow

![MCP Inspector Interface](./assets/mcp-inspector.png)

#### 📬 **Postman Collection** (Coming Soon)
A comprehensive Postman collection will be provided for:
- Complete API testing workflows
- Pre-configured requests for all endpoints
- Example payloads and responses
- Integration testing scenarios

## 🏗️ Workshop Architecture

### **Gravitee API Management Stack**
- **MongoDB**: Data storage for API configurations
- **Elasticsearch**: Analytics and logging storage  
- **API Gateway**: Secure API exposure with policies
- **Management API**: Configuration and policy management
- **Console UI**: API management interface
- **Portal UI**: Developer portal for API discovery

### **AI Agent Mesh**
- **A2A Agent**: Hotel booking agent following A2A protocol
- **Ollama LLM**: Local qwen3:0.6b model for natural language processing
- **Hotel API**: Demo API with embedded MCP server
- **MCP Inspector**: Visual MCP protocol debugging and inspection
- **A2A Inspector**: Visual A2A protocol testing and debugging

## 🛡️ Key Features Demonstrated

### **1. Agent Security & Policies**
- **Token Tracking**: Monitor and track AI agent API usage
- **Guard Rails Policy**: Implement safety controls for AI interactions
- **Secure Agent Exposure**: Protect LLMs behind Gravitee gateway

### **2. Agent Discovery & Catalog**
- **A2A Agent Cards**: Standardized agent capability discovery
- **Unified Agent Catalog**: Centralized registry of available agents
- **Capability Mapping**: Understand what each agent can do

### **3. API-to-Agent Integration**  
- **MCP Server Embedding**: Make existing APIs discoverable to AI
- **Tool Discovery**: Agents automatically find available API tools
- **OpenAPI Integration**: OAS-described APIs become AI-accessible


## 🧪 Workshop Scenarios

### **Scenario 1: Agent Discovery & Testing**
1. **A2A Inspector**: Open http://localhost:8005
   - Configure Agent Card URL: `http://apim-gateway:8082/agents/bookings/v1/.well-known/agent-card.json`
   - Explore agent capabilities and metadata
   - Test interactive conversations: *"List the hotel bookings options please"*
2. **Gravitee Console**: Browse to http://localhost:8084
   - Import the A2A Agent via its Agent Card
   - Configure security policies for the agent

### **Scenario 2: Tool Discovery & Integration**
1. **MCP Inspector**: Open http://localhost:6274
   - Select "Streamable HTTP" protocol
   - Connect to MCP server: `http://apim-gateway:8082/bookings/mcp`
   - List available tools and test tool calls interactively
2. **See Integration**: Watch how the A2A Agent uses these tools
   - Use A2A Inspector to see real-time tool usage during conversations
   - Understand the MCP-to-Agent communication flow through the gateway

### **Scenario 3: Secure Agent Exposure**
1. **Gravitee Management**: 
   - Create API definition for the A2A Agent
   - Apply Token Tracking policy to monitor usage
   - Apply Guard Rails policy for safety controls
2. **Test Security**: Use inspectors to verify policy enforcement
   - Monitor token usage through Gravitee analytics
   - Test guard rail behaviors with various inputs

## 🛑 Stop the Workshop

```bash
docker compose down
```

## 🎓 Learning Outcomes

After completing this workshop, you'll understand:

- ✅ How to **securely expose AI agents** through Gravitee
- ✅ How to implement **AI-specific policies** (Token Tracking, Guard Rails)
- ✅ How to create a **unified agent catalog** using A2A protocol
- ✅ How to make **existing APIs discoverable** to AI agents via MCP
- ✅ How to **integrate LLMs** with enterprise API management
- ✅ How to use **visual inspectors** for protocol debugging and testing
- ✅ How to **interactively test agents** without command-line tools

## 🛠️ Development Tools

This workshop provides modern development and testing tools:

- **Visual Protocol Inspection**: Both A2A and MCP protocols have dedicated visual inspectors
- **Interactive Testing**: No curl commands needed - use browser-based interfaces
- **Real-time Debugging**: See protocol messages and agent behavior in real-time
- **Comprehensive Tooling**: Postman collections and visual inspectors cover all testing scenarios

**Ready to explore the future of AI Agent Management? Let's go! 🚀**