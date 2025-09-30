# Gravitee Hands-On AI Workshop : The Hotel Booking AI Revolution 🏨🤖


## 🎯 What You'll Learn : Going From Traditional API to Intelligent Agent

Imagine you're working for **BookEasy**, a rapidly growing hotel booking platform. Your customers love your service, but they're asking for something more - they want to interact with your platform naturally, asking questions like *"Find me a pet-friendly hotel in Paris for next weekend"* or *"Show me all my bookings and cancel the one in London."*

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

Before we begin BookEasy's AI transformation, we need to prepare our development environment. Think of this as setting up your innovation lab where you'll experiment with cutting-edge AI agent technology.

### 1. Unlock Gravitee Enterprise AI Features

Your AI transformation requires enterprise-grade capabilities - token tracking, AI guard rails, and advanced agent management. These features are available with a **Gravitee Enterprise License**.

> **⚠️ Enterprise License Required**: The AI policies and agent mesh features demonstrated in this workshop require a **Gravitee Enterprise License**.
> 
> **📞 Need a License?** If you don't have one, contact Gravitee [here](https://www.gravitee.io/contact-us) to get started.

**🔑 Generate Base64 License Key** 

You need to convert your `license.key` file into a base64 string before using it in the workshop.

* Linux:
```bash
base64 -w 0 license.key
```

* MacOS:
```bash
base64 -i license.key | tr -d '\n'
```

The command will print a long string ending with `=`, which is your base64 license.
Copy this string and use it in the next steps.

#### Option A: Environment Variable
```bash
export GRAVITEE_LICENSE="PUT_YOUR_BASE64_LICENSE_HERE"
```

#### Option B: Using .env File (Recommended)

The `.env-template` file contains all necessary environment variables with default values.
Rename or copy the `.env-template` to a `.env` file and simply replace `PUT_YOUR_BASE64_LICENSE_HERE` with your actual base64-encoded license key.

### 2. Launch Your AI Transformation Environment

With your license configured, it's time to spin up BookEasy's complete AI-enabled infrastructure. This includes your existing hotel booking API, a local AI model, the Gravitee API Gateway with AI features, and powerful debugging tools.

```bash
docker compose up -d
```

*Grab a coffee ☕ - it takes 2-3 minutes for all services to start and the AI model to download.*

### 3. Your Environment

| Service | URL | Description |
|---------|-----|-------------|
| **Gravitee Console** | http://localhost:8084 | API Management Console |
| **Gravitee Portal** | http://localhost:8085 | Developer Portal |
| **Hotel Booking API** | http://localhost:8082/bookings | Demo API *(available only during the workshop)* |
| **Hotel Booking Agent** | http://localhost:8082/bookings-agent | AI Agent (A2A Protocol) *(available only during the workshop)* |
| **MCP Inspector** | http://localhost:6274 | Visual MCP Protocol Inspector |
| **A2A Inspector** | http://localhost:8004 | Visual A2A Protocol Inspector |

#### 📬 **Postman Collection** (Coming Soon)
A comprehensive Postman collection will be provided for:
- Complete API testing workflows
- Pre-configured requests for all endpoints
- Example payloads and responses
- Integration testing scenarios

## 📖 BookEasy's AI Transformation Workshop

Your journey unfolds across three critical phases, each building upon the last to create a complete AI-powered hotel booking experience.

### **Part 1: Making Your API AI-Ready 🔧**

*The Challenge: Your existing hotel booking REST API is perfect for traditional applications, but AI agents can't discover or understand how to use it. You need to bridge this gap.*

**Your Mission**: Transform your conventional REST API into an AI-discoverable service using the Model Context Protocol (MCP). This will allow AI agents to automatically discover and understand what your API can do and how to interact with it.

> **💡 Shortcut:** You can import the preconfigured API definition from [`Hotel-Booking-API-1-0.json`](./apim-apis-definitions/Hotel-Booking-API-1-0.json) directly into Gravitee to save time.  
> - In the Gravitee Console, go to **APIs → Import** and select the JSON file.
> - This will set up the Hotel Booking API with the MCP entrypoint and tool mappings automatically.

**🛠️ Technical Implementation:**

1. **Create Your AI-Ready API Gateway**: Set up a new V4 API named `Hotel Booking API` (Version `1.0`) as an HTTP Proxy
2. **Configure the Bridge**: Point your entrypoint (`/bookings`) to your existing service (`http://hotel-booking-api:8000/bookings`)
3. **Enable MCP Magic**: 
   - Navigate to the "MCP Entrypoint" tab and enable it on the `/mcp` path
   - Import your OpenAPI specification from [`hotel-booking-1-0.yaml`](./hotel-booking-api/hotel-booking-1-0.yaml)
   - *This is where the magic happens - Gravitee automatically converts your REST API into MCP tools!*

**🕵️ Test Your Transformation:**

Open the **MCP Inspector** at http://localhost:6274 to see your API through an AI agent's eyes:
- Select "Streamable HTTP" protocol
- Connect to your new MCP server: `http://apim-gateway:8082/bookings/mcp`
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

**Your Mission**: Deploy BookEasy's intelligent hotel booking agent that customers can chat with naturally. The agent will automatically discover and use your hotel booking tools while being fully monitored and secured.

> **💡 Shortcut:** You can import the preconfigured API definition from [`Hotel-Booking-AI-Agent-1-0.json`](./apim-apis-definitions/Hotel-Booking-AI-Agent-1-0.json) directly into Gravitee to save time.  
> - In the Gravitee Console, go to **APIs → Import** and select the JSON file.
> - This will set up the Hotel Booking AI Agent API automatically.

**🛠️ Technical Implementation:**

1. **Deploy Your Intelligent Agent**: Create a V4 API named `Hotel Booking AI Agent` (Version `1.0`) using the **Agent Proxy** type
2. **Connect Agent to Gateway**: Point entrypoint `/bookings-agent` to `http://hotel-booking-a2a-agent:8001`
3. **Agent Goes Live**: Your agent is now running and ready to help customers!

**🗣️ Experience the Magic with A2A Inspector:**

Visit http://localhost:8004 to chat with your newly deployed AI agent:

1. **Connect to Your Agent**: Use the agent card URL:
   ```
   http://apim-gateway:8082/bookings-agent/.well-known/agent-card.json
   ```
2. **Discover Capabilities**: See what your agent can do - it automatically knows about your hotel booking tools
3. **Start Conversations**: Try natural language queries like:
   - *"List the hotel booking options please"*
   - *"Find me a hotel in Paris for 2 nights"*
   - *"Show me my current bookings"*
4. **Watch the Protocol**: See real-time A2A protocol messages as your agent thinks and acts

![A2A Inspector Interface](./assets/a2a-inspector.png)

*🎉 **Success Milestone**: BookEasy customers can now chat naturally with AI to book hotels - your transformation is complete!*

## 🏁 Wrapping Up

When you're done exploring BookEasy's new AI-powered future:

```bash
docker compose down
```

## 🎓 What You've Accomplished

**Congratulations! 🎉** You've just completed a complete AI transformation journey. Here's what BookEasy (and you) now have:

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

## 🚀 Your AI Journey Continues...

**BookEasy's transformation is complete**, but this is just the beginning. You now have the foundation to:

- **🔄 Add More Agents**: Build travel, restaurant, or activity booking agents using the same patterns
- **🏢 Scale Across Enterprise**: Roll out AI agents for different business units with proper security
- **📈 Optimize & Monitor**: Use the analytics to improve agent performance and control costs
- **🛡️ Enhance Security**: Implement OAuth, JWT, and advanced AI safety measures

**The future of customer experience is conversational, and you're now ready to build it! 🌟**

*Ready to revolutionize how your customers interact with your platform? The tools are in your hands!* 🛠️✨
