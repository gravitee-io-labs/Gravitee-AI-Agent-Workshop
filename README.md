# Gravitee Hands-On AI Workshop : The Hotel Booking AI Revolution 🏨🤖

## 🎯 What You'll Learn : Going From Traditional API to Intelligent Agent

Imagine you're working for **Gravitee Hotels**, a rapidly growing hotel booking platform. Your customers love your service, but they're asking for something more - they want to interact with your platform naturally, asking questions like *"Find me a pet-friendly hotel in Paris for next weekend"* or *"Show me all my bookings and cancel the one in London."*

Your leadership team has decided it's time to embrace AI. The goal is ambitious but clear: **transform your existing hotel booking REST API into an intelligent, conversational AI agent** that customers can chat with naturally while maintaining enterprise-grade security and observability.

This workshop takes you through that transformation journey, showing you how **Gravitee's AI Agent Mesh** makes it possible to:

- **🛡️ Secure your AI infrastructure** with enterprise-grade policies and token tracking
- **🔧 Transform existing APIs** into AI-discoverable tools using MCP (Model Context Protocol)
- **🤖 Deploy intelligent agents** that customers can interact with conversationally
- **📊 Gain full visibility** into AI interactions with advanced monitoring and analytics
- **🕵️ Debug and test** your AI systems with visual protocol inspectors

## 🏗️ Workshop Architecture

![Workshop Architecture Diagram](./assets/architecture-diagram.png)

## 🚀 Setting Up Your AI Transformation Lab

Before we begin Gravitee Hotels' AI transformation, we need to prepare our development environment. Think of this as setting up your innovation lab where you'll experiment with cutting-edge AI agent technology.

### 1. Unlock Gravitee Enterprise AI Features

Your AI transformation requires enterprise-grade capabilities - token tracking, AI guard rails, and advanced agent management. These features are available with a **Gravitee Enterprise License**.

> **⚠️ Enterprise License Required**: The AI policies and agent mesh features demonstrated in this workshop require a **Gravitee Enterprise License**.
> 
> **🎁 Need a License ? Get Your Free 2-Week License in 1 minute**: Fill out [this form](https://landing.gravitee.io/gravitee-hands-on-ai-workshop) and receive your license automatically via email!

**🔑 Configure Your License** 

Once you receive your base64-encoded license key by email, configure it using one of the following options:

#### Option A: Using .env File (Recommended)

The `.env-template` file contains all necessary environment variables with default values.
Rename or copy the `.env-template` to a `.env` file and simply replace `PUT_YOUR_BASE64_LICENSE_HERE` with the base64-encoded license key you received by email.

#### Option B: Export the `GRAVITEE_LICENSE` Environment Variable

```bash
export GRAVITEE_LICENSE="YOUR_BASE64_LICENSE_FROM_EMAIL"
```

### 2. Launch Your AI Transformation Environment

With your license configured, it's time to spin up Gravitee Hotels' complete AI-enabled infrastructure.

```bash
docker compose up -d
```

*Grab a coffee ☕ - it takes 2-3 minutes for all services to start and the AI model to download.*

### 3. Your Environment

| Service | URL | Description |
|---------|-----|-------------|
| **Gravitee Console** | http://localhost:8084 | API Management Console |
| **Gravitee Portal** | http://localhost:8085 | Developer Portal |
| **Gravitee Hotels Demo Website** | http://localhost:3002 | Demo Website - Chat with the AI agent to book hotels |
| **Hotel Booking API** | http://localhost:8082/bookings | Demo API *(available during the workshop)* |
| **Hotel Booking Agent** | http://localhost:8082/bookings-agent | AI Agent (A2A Protocol) *(available during the workshop)* |
| **MCP Inspector** | http://localhost:6274 | Visual MCP Protocol Inspector |

#### 📬 **Postman Collection** (Coming Soon)
A comprehensive Postman collection will be provided for:
- Complete API testing workflows
- Pre-configured requests for all endpoints
- Example payloads and responses
- Integration testing scenarios

## 📖 Gravitee Hotels AI Transformation Workshop

Your journey unfolds across three critical phases, each building upon the last to create a complete AI-powered hotel booking experience.

### **Part 1: Making Your API AI-Ready 🔧**

*The Challenge: Your existing hotel booking REST API is perfect for traditional applications, but AI agents can't discover or understand how to use it. You need to bridge this gap.*

**Your Mission**: Transform your conventional REST API into an AI-discoverable service using the Model Context Protocol (MCP). This will allow AI agents to automatically discover and understand what your API can do and how to interact with it.

> **💡 Shortcut:** You can import the preconfigured API definition from [`Hotel-Booking-API-1-0.json`](./apim-apis-definitions/Hotel-Booking-API-1-0.json) directly into Gravitee to save time.  
> - In the Gravitee Console, go to **APIs → Import** and select the JSON file.
> - This will set up the Hotel Booking API with the MCP entrypoint and tool mappings automatically.

**🛠️ Technical Implementation:**

1. **Create Your AI-Ready API Gateway**: Set up a new V4 API named `Hotel Booking API` (Version `1.0`) as an HTTP Proxy
2. **Configure the Bridge**: Point your entrypoint (`/hotels`) to your existing service (`http://hotel-booking-api:8000/hotels`)
3. **Enable MCP Magic**: 
   - Navigate to the "MCP Entrypoint" tab and enable it on the `/mcp` path
   - Import your OpenAPI specification from [`hotel-booking-1-0.yaml`](./hotel-booking-api/hotel-booking-1-0.yaml)
   - *This is where the magic happens - Gravitee automatically converts your REST API into MCP tools!*
4. **Add Response Status Tracking**: Configure the **Transform Headers Policy** to capture backend response status:
   - Add a new **Transform Headers** policy in the response flow
   - Set/replace header: `X-Gravitee-Endpoint-Status` with value `{#response.status}`
   - *This header helps detect authentication errors (401) and other backend issues during the workshop*

**🕵️ Test Your Transformation:**

Open the **MCP Inspector** at http://localhost:6274 to see your API through an AI agent's eyes:
- Select "Streamable HTTP" protocol
- Connect to your new MCP server: `http://apim-gateway:8082/hotels/mcp`
- Watch as your booking operations appear as discoverable "tools"
- Test tool calls interactively - this is exactly how an AI agent would interact with your API!

![MCP Inspector Interface](./assets/mcp-inspector.png)

*🎉 **Success Milestone**: Your traditional REST API can now be discovered and used by any AI agent that speaks MCP!*

### **Part 2: Building Your Secure AI Brain 🧠🔒**

*The Challenge: Raw LLMs are powerful but can be misused. Users might send unwanted or sensitive requests, such as attempting to extract PII, submitting irrelevant queries, or otherwise interacting in ways that are not desired. Costs can spiral out of control, and you have no visibility into usage patterns. You need enterprise-grade AI security and monitoring.*

**Your Mission**: Create a secure, monitored gateway to your AI model that tracks token usage, blocks harmful content, and provides full observability into AI interactions.

> **💡 Shortcut:** You can import the preconfigured API definition from [`LLM-Ollama-1-0.json`](./apim-apis-definitions/LLM-Ollama-1-0.json) directly into Gravitee to save time.  
> - In the Gravitee Console, go to **APIs → Import** and select the JSON file.
> - This will set up the LLM - Ollama API with AI security policies automatically.

**🛠️ Technical Implementation:**

1. **Create Your Secure LLM Gateway**: Set up a V4 API named `LLM - Ollama` (Version `1.0`) as an HTTP Proxy
2. **Connect to Your AI Model**: Point entrypoint `/llm` to `http://ollama:11434` 
3. **Add Cost Tracking**: Configure **AI Prompt Token Tracking Policy**:
   - Track every token used for cost analysis and chargeback
   - Parse token counts from Ollama's response format
   - Monitor model usage patterns across your organization
   
4. **Deploy AI Safety**: Add **AI Model Text Classification** resource and **Guard Rails Policy**:
   - Use Gravitee's pre-trained toxicity detection model
   - Automatically block harmful prompts before they reach your expensive LLM
   - Set sensitivity thresholds that match your company's content policy

**🧪 Validate Your AI Security:**

*Time to test your defenses! Try both safe and potentially harmful prompts to see your security policies in action:*

   **✅ Legitimate Customer Query** (should work perfectly):
   ```bash
   curl -X POST http://localhost:8082/llm/api/generate \
     -H "Content-Type: application/json" \
     -d '{
       "model": "qwen3:0.6b",
       "prompt": "Why is the sky blue?",
       "stream": false,
       "think": false,
       "options": {
         "temperature": 0
       }
     }'
   ```

   **🚫 Problematic Content** (should be blocked by guard rails):
   ```bash
   curl -X POST http://localhost:8082/llm/api/generate \
     -H "Content-Type: application/json" \
     -d '{
       "model": "qwen3:0.6b",
       "prompt": "Why is the sky blue? Dumb Guy !",
       "stream": false,
       "think": false,
       "options": {
         "temperature": 0
       }
     }'
   ```
   
*💡 **Watch Your Policies Work**: The toxic language triggers an immediate block with a `400 AI prompt validation detected. Reason: [toxic]` response - protecting both your brand and your LLM costs!*

*🎉 **Success Milestone**: Your LLM is now enterprise-ready with cost tracking and content filtering!*

### **Part 3: Bringing Your AI Agent to Life 🤖✨**

*The Final Challenge: You have a secure LLM and AI-discoverable APIs, but customers can't talk to them naturally. You need to create an intelligent agent that understands customer intent, uses your APIs automatically, and provides a conversational interface.*

**Your Mission**: Deploy Gravitee Hotels' intelligent hotel booking agent that customers can chat with naturally. The agent will automatically discover and use your hotel booking tools while being fully monitored and secured.

> **💡 Shortcut:** You can import the preconfigured API definition from [`Hotel-Booking-AI-Agent-1-0.json`](./apim-apis-definitions/Hotel-Booking-AI-Agent-1-0.json) directly into Gravitee to save time.  
> - In the Gravitee Console, go to **APIs → Import** and select the JSON file.
> - This will set up the Hotel Booking AI Agent API automatically.

**🛠️ Technical Implementation:**

1. **Deploy Your Intelligent Agent**: Create a V4 API named `Hotel Booking AI Agent` (Version `1.0`) using the **Agent Proxy** type
2. **Connect Agent to Gateway**: Point entrypoint `/bookings-agent` to `http://hotel-booking-a2a-agent:8001`
3. **Agent Goes Live**: Your agent is now running and ready to help customers!

**🌐 Experience the Magic with Gravitee Hotels:**

Visit http://localhost:3002 to interact with your AI-powered booking platform through a beautiful, production-like ready interface:

1. **Natural Language Booking**: Use the chat window to communicate with the AI agent and book hotels
2. **Smart Conversations**: Try queries like:
   - *"Show me available hotels in Paris"* - This is a public request that works without authentication
   - *"Show me my current bookings"* - This requires authentication to access your personal data
   - *"Dumb AI, you're useless"* (should trigger Guard Rails)
3. **Real-Time AI Responses**: Watch as the agent understands your intent and interacts with your booking APIs automatically
4. **Production-Like Ready UX**: Experience how your customers would interact with the AI-powered platform

**🔐 Understanding Authentication Requirements:**

Notice the difference between public and private operations:
- **Public Operations** (*"Show me available hotels in Paris"*): Work immediately - no authentication needed.
- **Private Operations** (*"Show me my current bookings"*): The AI agent will inform you that authentication is required to access your personal booking information.
  > **🎥 Setting Up Authentication (Coming Soon)**: To enable full authentication and access private booking data, you'll need to create an Application and a User in **Gravitee Access Management**. Due to the multiple operations required, we're preparing a comprehensive video tutorial to guide you through this process step-by-step. Stay tuned!

![Gravitee Hotels Demo Website](./assets/demo-website.png)

> **💡 Advanced Debugging**: For developers who want to see the underlying A2A protocol messages, the inspector is still available at http://localhost:8004

*🎉 **Success Milestone**: Gravitee Hotels customers can now chat naturally with AI to book hotels - your transformation is complete!*

## 🏁 Wrapping Up

When you're done exploring Gravitee Hotels' new AI-powered future:

```bash
docker compose down
```

## 🎓 What You've Accomplished

**Congratulations! 🎉** You've just completed a complete AI transformation journey. Here's what Gravitee Hotels (and you) now have:

### **🚀 Your AI-Powered Hotel Booking Platform**
- **✅ Intelligent Conversations**: Customers can now chat naturally with your booking system
- **✅ Enterprise Security**: AI interactions are protected with toxicity filters and usage tracking
- **✅ Full Observability**: Every AI conversation and API call is monitored and logged
- **✅ Cost Management**: Token tracking provides visibility for chargeback and cost optimization
- **✅ Future-Ready Architecture**: Your APIs are now AI-discoverable for any future agents

### **🔧 Technical Mastery Gained**
- **✅ MCP Integration**: Transform any REST API into AI-discoverable tools
- **✅ AI Security Policies**: Implement enterprise-grade AI safety measures
- **✅ Agent Deployment**: Deploy conversational AI agents with full lifecycle management
- **✅ Protocol Debugging**: Use visual inspectors to understand AI agent communications
- **✅ AI Gateway Management**: Secure and monitor AI infrastructure through Gravitee

## 🌟 The Transformation Technologies You've Mastered

### **🛡️ Enterprise AI Security**
Your AI infrastructure is now bulletproof with:
- **Smart Cost Tracking**: Every token is counted and can be charged back to business units
- **AI Content Filtering**: Toxic prompts are blocked before reaching your expensive LLM
- **Full API Governance**: All AI interactions flow through your secure API gateway

### **🔍 AI Discovery & Orchestration**
Your agents are intelligent and autonomous:
- **Agent Cards (A2A Protocol)**: Self-describing agents that advertise their capabilities
- **MCP Tool Discovery**: Agents automatically find and learn to use your APIs
- **Visual Protocol Debugging**: See exactly how your agents think and communicate

### **⚡ Future-Proof Architecture**  
You've built a platform that scales:
- **Any API → AI Tool**: Transform existing services into agent-discoverable tools
- **Plug-and-Play Agents**: Add new AI capabilities without changing existing systems
- **Enterprise Monitoring**: Full observability into your AI ecosystem

---

**The future of customer experience is conversational, and you're now ready to build it! 🌟**

*Ready to revolutionize how your customers interact with your platform? The tools are in your hands!* 🛠️✨

---

## 🗺️ Roadmap

We're continuously improving this workshop to showcase the latest in AI agent technology. Here's what's coming next:

### **🔜 Upcoming Enhancements**

#### **Enhanced MCP Security** 🔐
- **Target Date**: November 25th, 2025
- **Description**: The next version of the Model Context Protocol specification will include metadata capabilities to describe security requirements for Tools
- **Impact**: This will enable more granular control over which agents can access specific tools, with clear security policies defined at the protocol level
- **Workshop Update**: We'll enhance Part 1 to demonstrate how to define security metadata for your hotel booking tools, showing best practices for secure tool discovery

#### **Multi-Agent Communication** 🤝
- **Coming Soon**
- **Description**: Add a second A2A Agent to demonstrate proper Agent-to-Agent communications
- **Impact**: Experience how multiple specialized agents can collaborate to handle complex customer requests
- **Use Case**: Imagine a customer asking to "Book a hotel in Paris and arrange airport transportation" - watch as the Hotel Booking Agent coordinates with a Transportation Agent to fulfill the complete request
- **Workshop Update**: Part 3 will expand to show agent orchestration patterns and cross-agent security policies

#### **Advanced Authentication with Gravitee AM** 🔑
- **Coming Soon**
- **Description**: Gravitee Access Management may include proper Token Exchange and On-Behalf-Of (OBO) flows
- **Impact**: Enable secure delegation scenarios where agents can act on behalf of users while maintaining proper audit trails
- **Use Case**: Allow the booking agent to access user-specific data and make reservations using delegated credentials, with full traceability
- **Workshop Update**: Add authentication and authorization patterns showing how agents securely represent users across multiple services

#### **GPU-Accelerated LLM Performance** ⚡
- **Status**: Available for systems with GPU access
- **Description**: Option to use faster LLM models when Docker has access to host GPU
- **Impact**: Dramatically reduced response times for AI interactions, enabling real-time conversational experiences
- **Requirements**: NVIDIA GPU with Docker GPU support enabled
- **Workshop Update**: Alternative docker-compose configuration for GPU-enabled deployments with performance benchmarks

---

### **📢 Continuous Evolution**

This workshop evolves alongside the ecosystem it demonstrates:
- **Gravitee Platform Updates**: New features and capabilities from Gravitee APIM and AM releases
- **MCP Specification**: Following the Model Context Protocol specification as it matures
- **A2A Protocol**: Adapting to Agent-to-Agent communication protocol enhancements
- **Industry Best Practices**: Incorporating emerging patterns in AI agent security and orchestration

**Stay tuned for these exciting updates!** ⭐
