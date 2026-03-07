import os
import uuid
from typing import Dict, Any, Optional
import sys
import logging
import json

from dotenv import load_dotenv

# Add the client packages to Python path (Docker container paths)
sys.path.insert(0, '/app/hotel-booking-a2a-agent/mcp-client')
sys.path.insert(0, '/app/hotel-booking-a2a-agent/llm-client')

from mcp_client.main import MCPMultiClient
from llm_client.main import LLMClient, LLMRateLimitError
from agent_server.auth_service import AuthService, AuthenticationError
from agent_server.colored_logger import get_agent_logger
from agent_server.a2a_client import A2AAgentRegistry, A2AClientError

# A2A SDK imports
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Message, Role, TextPart

load_dotenv()

# Tool name used for A2A delegation — the LLM calls this to delegate to a remote agent
A2A_DELEGATION_TOOL_NAME = "delegate_to_a2a_agent"

# Configuration
AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
# Support both single URL (backward compatibility) and multiple URLs
MCP_HTTP_URLS = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", "http://gio-apim-gateway:8082/hotels/mcp"))
OIDC_DISCOVERY_URL = os.getenv("OIDC_DISCOVERY_URL", "http://am-gateway:8092/gravitee/oidc/.well-known/openid-configuration")

# Gravitee Access Management configuration
AM_TOKEN_URL = os.getenv("AM_TOKEN_URL", "http://gio-am-gateway:8092/gravitee/oauth/token")
AM_CLIENT_ID = os.getenv("AM_CLIENT_ID", "hotel-booking-agent")
AM_CLIENT_SECRET = os.getenv("AM_CLIENT_SECRET", "hotel-booking-agent")

# A2A Agent discovery configuration
# Base URLs of agents to discover at startup (comma-separated for multiple agents)
A2A_AGENT_URLS = os.getenv("A2A_AGENT_URLS", os.getenv("CURRENCY_AGENT_CARD_URL", "http://gio-apim-gateway:8082/currency-agent/.well-known/agent-card.json"))

logger = get_agent_logger(__name__)

# System prompt for the hotel booking agent
HOTEL_BOOKING_SYSTEM_PROMPT = (
    "You are a Hotel Booking AI Agent. Use the tools provided to help users find and book hotels.\n\n"
    "Hotel prices in the database are in EUR. If the user asks for prices in another currency, "
    "use the delegate_to_a2a_agent tool to convert them after retrieving hotel results.\n\n"
    "Whenever possible, personalize your responses using the guest's first name."
)

class HotelBookingAgent:
    """Hotel Booking Agent that uses MCP tools via LLM."""
    
    def __init__(self):
        self.mcp_client = MCPMultiClient(mcp_urls=MCP_HTTP_URLS)
        self.llm_client = LLMClient()
        self.auth_service = AuthService(
            oidc_discovery_url=OIDC_DISCOVERY_URL,
            am_token_url=AM_TOKEN_URL,
            am_client_id=AM_CLIENT_ID,
            am_client_secret=AM_CLIENT_SECRET
        )
        self.system_prompt = HOTEL_BOOKING_SYSTEM_PROMPT
        self._initialized = False
        
        # A2A agent registry for dynamic discovery of remote agents
        self.agent_registry = A2AAgentRegistry()
        
    async def initialize(self):
        """Initialize the agent by connecting to MCP servers, auth service, and discovering remote A2A agents."""
        try:
            # Connect with limited retries during startup to fail fast
            await self.mcp_client.connect_all(max_retries=3, connection_timeout=15)
            await self.auth_service.initialize()
            
            # Discover remote A2A agents from configured URLs
            await self._discover_a2a_agents()
            
            self._initialized = True
            logger.info("Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise

    async def _discover_a2a_agents(self):
        """
        Discover remote A2A agents from configured URLs.
        
        For each URL, the registry will:
        1. Fetch the Agent Card via the A2A discovery mechanism
        2. Parse the agent's name, skills, and capabilities
        3. Register it for later use
        
        After discovery, agents are available via the registry's
        find_agent_with_skill() method — no hard-coding needed.
        """
        if not A2A_AGENT_URLS:
            logger.info("No A2A agent URLs configured, skipping discovery")
            return

        urls = [url.strip() for url in A2A_AGENT_URLS.split(",") if url.strip()]
        
        for url in urls:
            # Strip the well-known path if present — the SDK adds it automatically
            base_url = url.replace("/.well-known/agent.json", "").replace("/.well-known/agent-card.json", "")
            
            try:
                agent = await self.agent_registry.discover_agent(base_url)
                if agent:
                    logger.info(f"Discovered A2A agent: '{agent.name}' with skills: {[s.name for s in agent.skills if hasattr(s, 'name')]}")
            except Exception as e:
                logger.warning(f"Failed to discover agent at {url}: {e}. Continuing without it.")

        # Log summary of discovered agents
        discovered = self.agent_registry.list_agents()
        if discovered:
            logger.info(f"A2A Discovery complete: {len(discovered)} agent(s) discovered: {discovered}")
            logger.info(self.agent_registry.get_discovered_skills_description())
        else:
            logger.info("A2A Discovery complete: no agents discovered")

    def _build_a2a_delegation_tool(self) -> Optional[dict]:
        """
        Build an OpenAI-compatible tool definition for A2A delegation.
        
        This dynamically generates a tool that the LLM can call to delegate
        tasks to any discovered remote A2A agent. The tool description includes
        the list of available agents and their skills, so the LLM knows exactly
        when and how to use it.
        
        Returns:
            An OpenAI tool definition dict, or None if no agents are available.
        """
        agents = self.agent_registry.get_all_agents()
        if not agents:
            return None

        # Build a rich description listing all discovered agents and skills
        agent_descriptions = []
        agent_names = []
        for agent in agents:
            agent_names.append(agent.name)
            skills_text = agent.get_skill_descriptions()
            agent_descriptions.append(
                f"- '{agent.name}': {agent.description}\n  Skills:\n{skills_text}"
            )

        agents_info = "\n".join(agent_descriptions)

        return {
            "type": "function",
            "function": {
                "name": A2A_DELEGATION_TOOL_NAME,
                "description": (
                    "Delegate a task to an external agent discovered via the A2A protocol. "
                    "Use this to convert currency, for example after retrieving hotel prices in EUR.\n\n"
                    f"Available agents:\n{agents_info}\n\n"
                    "Send a clear, self-contained natural language message to the agent."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": f"The name of the agent to delegate to. Must be one of: {agent_names}",
                            "enum": agent_names,
                        },
                        "message": {
                            "type": "string",
                            "description": "The natural language message to send to the agent.",
                        },
                    },
                    "required": ["agent_name", "message"],
                },
            },
        }

    async def _handle_a2a_delegation(self, agent_name: str, message: str) -> dict:
        """
        Execute an A2A delegation call to a remote agent.
        
        Args:
            agent_name: The name of the agent to delegate to.
            message: The message to send.
            
        Returns:
            A tool result dict compatible with the LLM tool result format.
        """
        agent = self.agent_registry.get_agent(agent_name)
        if not agent:
            error_msg = f"Agent '{agent_name}' not found. Available agents: {self.agent_registry.list_agents()}"
            logger.error(error_msg)
            return {"content": [{"type": "text", "text": error_msg}], "isError": True}

        try:
            logger.info(f"[A2A Delegation] Sending to '{agent_name}': {message}")
            response_text = await agent.send_message(message)
            logger.info(f"[A2A Delegation] Response from '{agent_name}': {response_text}")
            return {"content": [{"type": "text", "text": response_text}], "isError": False}
        except A2AClientError as e:
            error_msg = f"A2A agent '{agent_name}' returned an error: {e}"
            logger.error(error_msg)
            return {"content": [{"type": "text", "text": error_msg}], "isError": True}
        except Exception as e:
            error_msg = f"Failed to communicate with agent '{agent_name}': {e}"
            logger.error(error_msg)
            return {"content": [{"type": "text", "text": error_msg}], "isError": True}

    async def process_request(self, message: str, authorization_header: Optional[str] = None) -> str:
        """
        Process a hotel booking request using MCP tools and LLM.
        
        The LLM decides which tools to call — including the A2A delegation tool
        to communicate with remote agents. No manual orchestration of currency
        conversion or other external agent calls is needed.
        
        Args:
            message: The user's message.
            authorization_header: Optional OAuth2 bearer token.
        """
        try:
            logger.info("=" * 80)
            logger.info("Received new request")
            logger.info(f"User message: {message}")
            if authorization_header:
                logger.debug("Authorization header present (masked for security)")
            logger.info("=" * 80)
            
            # Build the system prompt dynamically
            dynamic_system_prompt = self.system_prompt
            
            # Get available tools from all MCP servers
            available_tools = await self.mcp_client.list_all_tools()
            logger.info(f"Retrieved {len(available_tools)} available MCP tools")

            # Dynamically add the A2A delegation tool if agents were discovered
            a2a_tool = self._build_a2a_delegation_tool()
            if a2a_tool:
                available_tools = list(available_tools) + [a2a_tool]
                logger.info(f"Added A2A delegation tool — {len(self.agent_registry.list_agents())} remote agent(s) available")

            # Get LLM response with potential tool calls
            logger.info("Querying LLM to determine action...")
            initial_content, tool_calls = await self.llm_client.process_query(
                message, 
                available_tools,
                system_prompt=dynamic_system_prompt
            )

            if not tool_calls:
                # No tools called — return direct response or fallback
                logger.warning("No tools were called by LLM")
                logger.info("=" * 80)
                if initial_content:
                    return initial_content
                return (
                    "I couldn't determine a clear action from your message. "
                    "Please rephrase or provide more details (for example: search for hotels in New York) "
                    "and I'll help you."
                )

            # ─── Tool execution loop ─────────────────────────────────────
            # The LLM decides which tools to call and in what order.
            # We loop up to MAX_TOOL_ROUNDS to avoid infinite chains.
            MAX_TOOL_ROUNDS = 5
            conversation_messages = [
                {"role": "system", "content": dynamic_system_prompt},
                {"role": "user", "content": message},
            ]

            for round_num in range(MAX_TOOL_ROUNDS):
                if not tool_calls:
                    break

                tool_call = tool_calls[0]
                tool_name = tool_call.get("function", {}).get("name")
                tool_args = tool_call.get("function", {}).get("arguments", {})
                
                logger.info(f"[Round {round_num + 1}] Executing tool: {tool_name}")

                if tool_name == A2A_DELEGATION_TOOL_NAME:
                    # ── A2A delegation ──
                    agent_name = tool_args.get("agent_name", "")
                    agent_message = tool_args.get("message", "")
                    tool_result = await self._handle_a2a_delegation(agent_name, agent_message)
                else:
                    # ── MCP tool call ──
                    tool_result, response_headers = await self.mcp_client.call_tool(tool_name, tool_args)
                    
                    # Check if authentication is required
                    is_error = False
                    if isinstance(tool_result, dict):
                        is_error = tool_result.get('isError', False)
                    elif hasattr(tool_result, 'isError'):
                        is_error = tool_result.isError
                    
                    if is_error:
                        logger.info(f"Tool {tool_name} requires authorization - retrying with auth")
                        try:
                            am_token, user_email = await self.auth_service.process_authorization_for_tool(authorization_header)
                            if isinstance(tool_args, dict) and "Authorization" in tool_args:
                                del tool_args["Authorization"]
                            extra_headers = {
                                "Authorization": f"Bearer {am_token}",
                                "sub-email": user_email
                            }
                            logger.info("Retrying tool call with authorization...")
                            tool_result, response_headers = await self.mcp_client.call_tool(tool_name, tool_args, extra_headers=extra_headers)
                        except AuthenticationError as auth_error:
                            logger.error(f"Authentication error: {auth_error}")
                            return (
                                "You need to be signed in to complete this action. "
                                "Please sign in and try again."
                            )

                # Append the assistant tool_call + tool result to conversation
                tool_result_str = json.dumps(tool_result) if isinstance(tool_result, (dict, list)) else str(tool_result)
                conversation_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call.get("id", f"call_{round_num}"),
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args) if isinstance(tool_args, dict) else str(tool_args),
                        },
                    }],
                })
                conversation_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", f"call_{round_num}"),
                    "content": tool_result_str,
                })

                # Ask the LLM for next step: it may generate a final answer or another tool call
                logger.info(f"[Round {round_num + 1}] Asking LLM for next step...")
                try:
                    next_response = self.llm_client.client.chat.completions.create(
                        model=self.llm_client.model,
                        messages=conversation_messages,
                        tools=available_tools if available_tools else None,
                        tool_choice="auto",
                        temperature=self.llm_client.temperature,
                    )
                except Exception as llm_err:
                    logger.error(f"LLM error in tool loop: {llm_err}")
                    return await self.llm_client.process_tool_result(
                        message, tool_call, tool_result,
                        system_prompt=dynamic_system_prompt
                    )

                next_message = next_response.choices[0].message

                if next_message.tool_calls:
                    # LLM wants to call another tool — continue the loop
                    tool_calls = []
                    for tc in next_message.tool_calls:
                        args = tc.function.arguments
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        tool_calls.append({
                            "id": tc.id,
                            "function": {
                                "name": tc.function.name,
                                "arguments": args,
                            },
                        })
                    logger.info(f"[Round {round_num + 1}] LLM wants to call: {[tc['function']['name'] for tc in tool_calls]}")
                else:
                    # LLM generated a final text response — we're done
                    final_response = next_message.content or ""
                    logger.info("Request processing completed successfully")
                    logger.info("=" * 80)
                    return final_response.strip()

            # Safety net: if we exhausted rounds, generate a final answer from what we have
            logger.warning(f"Reached max tool rounds ({MAX_TOOL_ROUNDS}), generating final response")
            final_response = self.llm_client.client.chat.completions.create(
                model=self.llm_client.model,
                messages=conversation_messages,
                temperature=self.llm_client.temperature,
            )
            result = final_response.choices[0].message.content or ""
            logger.info("=" * 80)
            return result.strip()
        
        except LLMRateLimitError as rate_error:
            logger.warning(f"Rate limit exceeded: {rate_error}")
            logger.info("=" * 80)
            
            # Build a user-friendly rate limit message
            message_parts = ["You've reached your request limit."]
            
            if rate_error.reset:
                try:
                    import time
                    # X-Token-Rate-Limit-Reset is a Unix timestamp in milliseconds
                    reset_timestamp_ms = int(rate_error.reset)
                    current_timestamp_ms = int(time.time() * 1000)
                    wait_ms = reset_timestamp_ms - current_timestamp_ms
                    
                    if wait_ms > 0:
                        wait_seconds = wait_ms // 1000
                        if wait_seconds >= 60:
                            minutes = wait_seconds // 60
                            seconds = wait_seconds % 60
                            if seconds > 0:
                                message_parts.append(f"Please wait {minutes} minute(s) and {seconds} second(s) before sending a new request.")
                            else:
                                message_parts.append(f"Please wait {minutes} minute(s) before sending a new request.")
                        else:
                            message_parts.append(f"Please wait {wait_seconds} second(s) before sending a new request.")
                    else:
                        message_parts.append("Please try again now.")
                except (ValueError, TypeError):
                    message_parts.append("Please wait a moment before trying again.")
            else:
                message_parts.append("Please wait a moment before trying again.")
            
            if rate_error.limit and rate_error.remaining:
                try:
                    limit = int(rate_error.limit)
                    remaining = int(rate_error.remaining)
                    message_parts.append(f"(Limit: {limit} tokens, Remaining: {remaining})")
                except (ValueError, TypeError):
                    pass
            
            return " ".join(message_parts)
            
        except Exception as e:
            logger.error(f"Error processing request: {e}", exc_info=True)
            logger.info("=" * 80)
            
            # Use LLM to generate a user-friendly error message
            try:
                error_prompt = (
                    f"An error occurred while processing a user request: {str(e)}\n\n"
                    "Generate a brief, friendly response for the user. "
                    "Do NOT include any technical details such as HTTP status codes, error codes, "
                    "reason codes, technical terms, or the original error message. "
                    "Just explain to the user what went wrong in simple terms."
                )
                friendly_error = await self.llm_client.process_query(
                    error_prompt,
                    [],
                    system_prompt=(
                        "You are a friendly hotel booking assistant. "
                        "When something goes wrong, respond naturally like a helpful human would. "
                        "Never mention technical details, technical error codes, HTTP statuse, etc.. "
                        "Keep responses short, warm, and helpful."
                    )
                )
                return friendly_error[0] if friendly_error[0] else "I'm sorry, something went wrong. Could you please try rephrasing your request?"
            except Exception as llm_error:
                logger.error(f"Failed to generate friendly error message: {llm_error}")
                return "I'm sorry, something went wrong. Could you please try rephrasing your request?"

    async def cleanup(self):
        """Clean up resources."""
        if self.mcp_client:
            await self.mcp_client.cleanup()
        if self.auth_service:
            await self.auth_service.cleanup()
        if self.agent_registry:
            await self.agent_registry.cleanup()

# Global agent instance (will be initialized by the executor)
hotel_agent = None

def create_agent_card() -> AgentCard:
    """Create the agent card for hotel booking management."""
    
    # Define the hotel booking skill — this is the NATIVE skill of this agent
    hotel_booking_skill = AgentSkill(
        id="skill_1_hotel_booking_management",
        name="hotel-booking-management",
        description="Comprehensive hotel booking management including searching, creating, updating, and canceling reservations",
        tags=["hotel", "booking", "management", "reservations"]
    )
    
    # NOTE: Currency conversion is NOT declared as a skill of this agent.
    # It is dynamically discovered at runtime via the A2A protocol by querying
    # remote agents' Agent Cards. This agent delegates to any discovered agent
    # that has a "currency"/"exchange" skill — following the A2A discovery pattern.
    
    # Define agent capabilities
    capabilities = AgentCapabilities(
        streaming=True,
        pushNotifications=False,
        stateTransitionHistory=True
    )
    
    # Create the agent card
    agent_card = AgentCard(
        name="Hotel Booking Manager",
        version="1.2.0",
        description=(
            "Expert hotel booking management agent. "
            "Can dynamically delegate to other agents discovered via the A2A protocol "
            "(e.g., currency conversion) based on their advertised skills."
        ),
        url="https://hotel-booking-agent.ai",
        capabilities=capabilities,
        skills=[hotel_booking_skill],
        defaultInputModes=['text/plain'],
        defaultOutputModes=['text/plain'],
        protocolVersion='0.3.0',
        preferredTransport='JSONRPC'
    )
    
    return agent_card

class HotelBookingRequestHandler(RequestHandler):
    """A2A compliant Hotel Booking Request Handler following best practices."""
    
    def __init__(self):
        """Initialize the Hotel Booking Request Handler."""
        self.agent = HotelBookingAgent()
        super().__init__()
        
    async def on_message_send(self, params, context):
        """Handle message/send requests."""
        try:
            # Initialize the agent if not already done
            if not hasattr(self.agent, '_initialized') or not self.agent._initialized:
                await self.agent.initialize()
                self.agent._initialized = True
            
            # Extract Authorization header from context
            authorization_header = None
            
            # Try to get from context.state['headers'] first (A2A SDK structure)
            if context and hasattr(context, 'state'):
                state = context.state
                if isinstance(state, dict) and 'headers' in state:
                    headers = state['headers']
                    authorization_header = headers.get('authorization') or headers.get('Authorization')
            
            # Fallback: try context.http_request if not found
            if not authorization_header and context and hasattr(context, 'http_request'):
                http_request = context.http_request
                if hasattr(http_request, 'headers'):
                    authorization_header = http_request.headers.get('Authorization') or http_request.headers.get('authorization')
            
            # Extract message content from A2A params
            user_message = ""
            if params.message.parts:
                for idx, part in enumerate(params.message.parts):
                    if hasattr(part, 'text'):
                        user_message = part.text # type: ignore
                        break
                    elif isinstance(part, dict):
                        if 'text' in part:
                            user_message = part['text']
                            break
                        elif part.get('type') == 'text' and 'value' in part:
                            user_message = part['value']
                            break
                    elif hasattr(part, '__dict__'):
                        part_dict = part.__dict__
                        if 'text' in part_dict:
                            user_message = part_dict['text']
                            break
                        elif 'root' in part_dict:
                            root_obj = part_dict['root']
                            if hasattr(root_obj, 'text'):
                                user_message = getattr(root_obj, 'text', '')
                                break
                            elif isinstance(root_obj, dict) and 'text' in root_obj:
                                user_message = root_obj['text']
                                break
                        
            if not user_message:
                logger.error("No message content provided in the request.")
                raise ValueError("No message content provided in the request.")
            
            # Process the request using the hotel agent with authorization header
            response_content = await self.agent.process_request(user_message, authorization_header)
            
            # Return A2A Message response
            return Message(
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[TextPart(text=response_content)]
            )
            
        except Exception as e:
            logger.error(f"Error in message handler: {e}")
            error_message = f"Sorry, I encountered an error while processing your request: {str(e)}"
            return Message(
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[TextPart(text=error_message)]
            )
    
    async def on_message_send_stream(self, params, context):
        """Handle message/stream requests."""
        # For simplicity, we'll use the same logic as send_message
        # In a real implementation, you might want to yield multiple events
        message_response = await self.on_message_send(params, context)
        yield message_response
    
    # Implement required methods with basic responses
    async def on_create_task(self, params, context=None):
        raise ServerError()
    
    async def on_list_tasks(self, params, context=None):
        return []
    
    async def on_get_task(self, params, context=None):
        raise ServerError()
    
    async def on_cancel_task(self, params, context=None):
        raise ServerError()
    
    async def on_set_task_push_notification_config(self, params, context=None):
        raise ServerError()
    
    async def on_get_task_push_notification_config(self, params, context=None):
        raise ServerError()
    
    async def on_resubscribe_to_task(self, params, context=None):
        raise ServerError()
    
    async def on_list_task_push_notification_config(self, params, context=None):
        return []
    
    async def on_delete_task_push_notification_config(self, params, context=None):
        return None

# Legacy handler for backward compatibility (if needed by A2A framework)
async def hotel_booking_handler(request: Dict[str, Any]) -> Dict[str, Any]:
    """Handler for hotel booking requests."""
    try:
        # Extract message from request
        message = request.get("message", "")
        authorization_header = request.get("authorization")
        
        if not message:
            return {
                "error": "No message provided",
                "status": "error"
            }
        
        # Process the request using the hotel agent
        response = await hotel_agent.process_request(message, authorization_header)
        
        return {
            "response": response,
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Error in hotel booking handler: {e}")
        return {
            "error": str(e),
            "status": "error"
        }

def create_app():
    """Create the A2A Starlette application following best practices."""
    
    # Create agent card
    agent_card = create_agent_card()
    
    # Create the request handler
    request_handler = HotelBookingRequestHandler()
    
    # Create the A2A application with proper RequestHandler
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler
    )
    
    # Build and return the Starlette app
    app = a2a_app.build()
    
    return app

async def startup():
    """Startup event handler."""
    logger.info("Starting Hotel Booking Agent Server...")
    try:
        # Initialize global hotel agent for backward compatibility
        global hotel_agent
        if hotel_agent is None:
            hotel_agent = HotelBookingAgent()
            await hotel_agent.initialize()
        logger.info("Server ready")
    except Exception as e:
        logger.error(f"Failed to start agent server: {e}")
        raise

async def shutdown():
    """Shutdown event handler."""
    logger.info("Shutting down Hotel Booking Agent Server...")
    global hotel_agent
    if hotel_agent:
        await hotel_agent.cleanup()
    logger.info("Hotel Booking Agent Server shut down")

def main():
    """Main entry point."""
    # Don't use basicConfig as we have custom colored loggers
    # Just ensure root logger is at INFO level
    logging.getLogger().setLevel(logging.INFO)
    
    app = create_app()
    
    # Add startup and shutdown events
    app.add_event_handler("startup", startup)
    app.add_event_handler("shutdown", shutdown)
    
    import uvicorn
    
    # Configure uvicorn to use our custom log config
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AGENT_SERVER_PORT,
        log_level="info",
        log_config=log_config
    )

if __name__ == "__main__":
    main()
