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

from mcp_client.main import MCPClient
from llm_client.main import LLMClient
from agent_server.auth_service import AuthService, AuthenticationError
from agent_server.colored_logger import get_agent_logger

# A2A SDK imports
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Message, Role, TextPart

load_dotenv()

# Configuration
AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", "http://gio-apim-gateway:8082/hotels/mcp")
OIDC_DISCOVERY_URL = os.getenv("OIDC_DISCOVERY_URL", "http://am-gateway:8092/gravitee/oidc/.well-known/openid-configuration")
JWT_SECRET = os.getenv("JWT_SECRET", "my-example-secret")

logger = get_agent_logger(__name__)

# System prompt for the hotel booking agent
HOTEL_BOOKING_SYSTEM_PROMPT = (
    "You are an Hotel Booking AI Agent whose only role is to use the tools provided.\n"
    "Always strictly follow this rule.\n"
    "Whenever possible, personalize your responses using the guest's first name to create a friendly experience."
)

class HotelBookingAgent:
    """Hotel Booking Agent that uses MCP tools via LLM."""
    
    def __init__(self):
        self.mcp_client = MCPClient(mcp_url=MCP_HTTP_URL)
        self.llm_client = LLMClient()
        self.auth_service = AuthService(
            oidc_discovery_url=OIDC_DISCOVERY_URL,
            jwt_secret=JWT_SECRET
        )
        self.system_prompt = HOTEL_BOOKING_SYSTEM_PROMPT
        self._initialized = False
        
    async def initialize(self):
        """Initialize the agent by connecting to MCP server and auth service."""
        try:
            await self.mcp_client.connect()
            await self.auth_service.initialize()
            self._initialized = True
            logger.info("Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise

    async def process_request(self, message: str, authorization_header: Optional[str] = None) -> str:
        """Process a hotel booking request using MCP tools and LLM."""
        validated_access_token: Optional[str] = None
        try:
            logger.info("=" * 80)
            logger.info("Received new request")
            logger.info(f"User message: {message}")
            if authorization_header:
                logger.debug("Authorization header present (masked for security)")
            logger.info("=" * 80)
            
            # Get available tools from MCP
            available_tools = await self.mcp_client.list_tools()
            logger.info(f"Retrieved {len(available_tools)} available tools from MCP")

            # Get LLM response with potential tool calls
            logger.info("Querying LLM to determine action...")
            initial_content, tool_calls = await self.llm_client.process_query(
                message, 
                available_tools,
                system_prompt=self.system_prompt
            )

            if tool_calls:
                # Handle the first tool call
                tool_call = tool_calls[0]
                tool_name = tool_call.get("function", {}).get("name")
                tool_args = tool_call.get("function", {}).get("arguments")

                # Define which tools are public (don't require authentication)
                PUBLIC_TOOLS = ["getAccommodations"]
                tool_requires_auth = tool_name not in PUBLIC_TOOLS

                # Validate the user's OAuth token before calling MCP tools (only for protected tools)
                if tool_requires_auth:
                    if not authorization_header:
                        logger.error(f"Tool {tool_name} requires authentication but no Authorization header was provided")
                        return (
                            "You need to be signed in to complete this action. "
                            "Please sign in and try again."
                        )

                    if validated_access_token is None:
                        try:
                            validated_access_token = await self.auth_service.process_authorization_for_tool(authorization_header)
                            logger.info("Validated user authorization token for tool call")
                        except AuthenticationError as auth_error:
                            logger.warning(f"Authorization failure while processing tool {tool_name}: {auth_error}")
                            return (
                                "I couldn't verify your sign-in status. "
                                "Please sign in again and then retry your request."
                            )
                else:
                    logger.info(f"Tool {tool_name} is public, skipping authentication")

                logger.info(f"Executing tool: {tool_name}")

                # Only add Authorization header for protected tools
                extra_headers = {}
                if tool_requires_auth and validated_access_token:
                    extra_headers["Authorization"] = f"Bearer {validated_access_token}"

                # Call the tool
                tool_result, response_headers = await self.mcp_client.call_tool(
                    tool_name,
                    tool_args,
                    extra_headers=extra_headers if extra_headers else None
                )

                # Check if the tool result contains an authorization/permission error
                tool_result_text = ""
                if isinstance(tool_result, dict) and "content" in tool_result:
                    for content_item in tool_result.get("content", []):
                        if isinstance(content_item, dict) and content_item.get("type") == "text":
                            tool_result_text += content_item.get("text", "")

                # Check for permission/authorization errors
                if any(error_indicator in tool_result_text.lower() for error_indicator in [
                    "forbidden", "unauthorized", "permission denied", "access denied",
                    "not authorized", "insufficient permissions", "authzen", "403", "401"
                ]):
                    logger.warning(f"Authorization denied for tool {tool_name}")
                    return "You don't have permissions to do this."

                # Check for invalid scope errors
                if "invalid_scope" in tool_result_text.lower() or "scope" in tool_result_text.lower():
                    logger.warning(f"Invalid scope for tool {tool_name}")
                    return "You don't have permissions to do this."

                # Get final response from LLM with tool result
                logger.info("Generating final response from LLM with tool result...")
                final_response = await self.llm_client.process_tool_result(
                    message,
                    tool_call,
                    tool_result,
                    system_prompt=self.system_prompt
                )
                
                logger.info("Request processing completed successfully")
                logger.info("=" * 80)
                return final_response

            # Return initial response if no tools were called
            logger.info("LLM provided direct response without tool calls")
            logger.info("=" * 80)
            # If LLM returned content without tools, it's likely a clarifying question or greeting
            if initial_content and initial_content.strip():
                return initial_content
            else:
                return (
                    "I'm here to help you with hotel bookings! I can:\n"
                    "• Search for hotels in any city\n"
                    "• Show your existing bookings\n"
                    "• Make new reservations\n"
                    "• Cancel bookings\n\n"
                    "What would you like to do?"
                )
            
        except Exception as e:
            logger.error(f"Error processing request: {e}", exc_info=True)
            logger.info("=" * 80)
            # Use LLM to generate a user-friendly error message
            try:
                error_prompt = (
                    f"An error occurred: {str(e)}\n\n"
                    "Generate a polite, user-friendly response explaining that something went wrong "
                    "and suggest what the user could try instead. Keep it brief and helpful."
                )
                friendly_error = await self.llm_client.process_query(
                    error_prompt,
                    [],
                    system_prompt="You are a helpful hotel booking assistant. Convert technical errors into friendly, actionable messages for users."
                )
                return friendly_error[0] if friendly_error[0] else f"Sorry, I encountered an error while processing your request: {str(e)}"
            except Exception as llm_error:
                logger.error(f"Failed to generate friendly error message: {llm_error}")
                return f"Sorry, I encountered an error while processing your request: {str(e)}"

    async def cleanup(self):
        """Clean up resources."""
        if self.mcp_client:
            await self.mcp_client.cleanup()
        if self.auth_service:
            await self.auth_service.cleanup()

# Global agent instance (will be initialized by the executor)
hotel_agent = None

def create_agent_card() -> AgentCard:
    """Create the agent card for hotel booking management."""
    
    # Define the hotel booking skill
    hotel_booking_skill = AgentSkill(
        id="skill_1_hotel_booking_management",
        name="hotel-booking-management",
        description="Comprehensive hotel booking management including searching, creating, updating, and canceling reservations",
        tags=["hotel", "booking", "management", "reservations"]
    )
    
    # Define agent capabilities
    capabilities = AgentCapabilities(
        streaming=True,
        pushNotifications=False,
        stateTransitionHistory=True
    )
    
    # Create the agent card
    agent_card = AgentCard(
        name="Hotel Booking Manager",
        version="1.0.0",
        description="Expert hotel booking management agent for comprehensive reservation handling",
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
