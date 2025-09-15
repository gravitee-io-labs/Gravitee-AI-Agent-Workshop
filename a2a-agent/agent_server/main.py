import asyncio
import os
import uuid
from typing import Dict, Any, List, Optional
import sys
import logging

from dotenv import load_dotenv

# Add the client packages to Python path (Docker container paths)
sys.path.insert(0, '/app/mcp-client')
sys.path.insert(0, '/app/llm-client')

from mcp_client.main import MCPClient
from llm_client.main import LLMClient

# A2A SDK imports
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Message, Role, TextPart

load_dotenv()

# Configuration
AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", "http://localhost:8082/bookings/mcp")

# Global clients
mcp_client = None
llm_client = None

logger = logging.getLogger(__name__)

class HotelBookingAgent:
    """Hotel Booking Agent that uses MCP tools via LLM."""
    
    def __init__(self):
        self.mcp_client = MCPClient(mcp_url=MCP_HTTP_URL)
        self.llm_client = LLMClient()
        
    async def initialize(self):
        """Initialize the agent by connecting to MCP server."""
        try:
            await self.mcp_client.connect()
            logger.info("Successfully connected to MCP server")
        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            raise

    async def process_request(self, message: str) -> str:
        """Process a hotel booking request using MCP tools and LLM."""
        try:
            # Get available tools from MCP
            available_tools = await self.mcp_client.list_tools()

            logger.info(f"Available tools: {available_tools}")
            logger.info(f"Processing request: {message}")
            # Get LLM response with potential tool calls
            initial_content, tool_calls = await self.llm_client.process_query(message, available_tools)

            if tool_calls:
                # Handle the first tool call
                tool_call = tool_calls[0]
                tool_name = tool_call.get("function", {}).get("name")
                tool_args = tool_call.get("function", {}).get("arguments")

                logger.info(f"Calling MCP tool: {tool_name}")
                
                # Call the tool using MCP
                tool_result = await self.mcp_client.call_tool(tool_name, tool_args)
                logger.info(f"Tool result: {tool_result}")

                # Get final response from LLM with tool result
                final_response = await self.llm_client.process_tool_result(message, tool_call, tool_result)
                return final_response

            # Return initial response if no tools were called
            return initial_content or "I'm here to help with hotel bookings. What can I do for you?"
            
        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return f"Sorry, I encountered an error while processing your request: {str(e)}"

    async def cleanup(self):
        """Clean up resources."""
        if self.mcp_client:
            await self.mcp_client.cleanup()

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
        
    async def on_message_send(self, params, context=None):
        """Handle message/send requests."""
        try:
            # Initialize the agent if not already done
            if hasattr(self.agent, 'mcp_client') and self.agent.mcp_client:
                # Check if MCP client is connected
                try:
                    await self.agent.mcp_client.list_tools()
                except:
                    await self.agent.initialize()
            else:
                await self.agent.initialize()
            
            # Extract message content from A2A params
            user_message = ""
            if params and 'message' in params:
                message = params['message']
                
                if hasattr(message, 'parts') and message.parts:
                    # Extract text from message parts - parts contain Part objects with root TextPart
                    for part in message.parts:
                        if hasattr(part, 'root') and part.root:
                            if hasattr(part.root, 'text') and part.root.text:
                                user_message += part.root.text
                        elif hasattr(part, 'text') and part.text:
                            user_message += part.text
                elif isinstance(message, dict) and 'parts' in message:
                    # Handle dict format
                    for part in message['parts']:
                        if isinstance(part, dict) and 'text' in part:
                            user_message += part['text']
                        elif hasattr(part, 'text'):
                            user_message += part.text
                        
            if not user_message:
                user_message = "How can I help you with hotel bookings today?"
            
            logger.info(f"Processing message: {user_message}")
            
            # Process the request using the hotel agent
            response_content = await self.agent.process_request(user_message)
            
            logger.info("Message processed successfully")
            
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
    
    async def on_message_send_stream(self, params, context=None):
        """Handle message/stream requests."""
        # For simplicity, we'll use the same logic as send_message
        # In a real implementation, you might want to yield multiple events
        message_response = await self.on_message_send(params, context)
        return [message_response]
    
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
        
        if not message:
            return {
                "error": "No message provided",
                "status": "error"
            }
        
        # Process the request using the hotel agent
        response = await hotel_agent.process_request(message)
        
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
        logger.info("Hotel Booking Agent Server started successfully")
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
    logging.basicConfig(level=logging.INFO)
    
    app = create_app()
    
    # Add startup and shutdown events
    app.add_event_handler("startup", startup)
    app.add_event_handler("shutdown", shutdown)
    
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AGENT_SERVER_PORT,
        log_level="info"
    )

if __name__ == "__main__":
    main()
