import asyncio
import os
import sys
import logging
import json
from typing import Optional, Dict, Any, List
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client as mcp_http_client
from dotenv import load_dotenv

# Add the agent-server package to Python path to access colored_logger
sys.path.insert(0, '/app/hotel-booking-a2a-agent/agent-server')

try:
    from agent_server.colored_logger import get_mcp_logger
    logger = get_mcp_logger(__name__)
except ImportError:
    # Fallback to standard logging if colored_logger is not available
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning("Colored logger not available, using standard logging")

load_dotenv()

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    logger.warning("httpx not available, response headers will not be captured")

MCP_HTTP_URL_DEFAULT = os.getenv("MCP_HTTP_URL", "http://gio-apim-gateway:8082/hotels/mcp")
MCP_RETRY_INTERVAL = int(os.getenv("MCP_RETRY_INTERVAL", "5"))  # seconds between retry attempts

class MCPClient:
    """MCP Client for discovering and calling tools from MCP servers."""
    
    def __init__(self, mcp_url: Optional[str] = None, retry_interval: int = MCP_RETRY_INTERVAL):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.transport_mode: str = "http"
        self.mcp_http_url: str = mcp_url or MCP_HTTP_URL_DEFAULT
        self.retry_interval: int = retry_interval
        self.is_connected: bool = False
        self.last_response_headers: Dict[str, str] = {}  # Store last response headers

    async def connect(self, url: Optional[str] = None, max_retries: Optional[int] = None):
        """
        Connect to MCP server using HTTP transport with retry logic.
        
        Args:
            url: Optional URL to override the default MCP server URL
            max_retries: Optional maximum number of retry attempts (None = infinite retries)
        """
        if url:
            self.mcp_http_url = url
        
        retry_count = 0
        while True:
            try:
                http_transport = await self.exit_stack.enter_async_context(
                    mcp_http_client(self.mcp_http_url)
                )
                try:
                    read_stream, write_stream, _ = http_transport  # type: ignore[misc]
                except Exception:
                    # Some versions might return only two values
                    read_stream, write_stream = http_transport  # type: ignore[misc]

                self.session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await self.session.initialize()
                
                self.is_connected = True
                logger.info(f"Connected to MCP Server at {self.mcp_http_url}")
                break
                
            except Exception as e:
                retry_count += 1
                error_msg = f"Failed to connect to MCP server at {self.mcp_http_url}: {e}"
                
                # Check if we've reached max retries
                if max_retries is not None and retry_count >= max_retries:
                    logger.error(f"{error_msg}. Max retries ({max_retries}) reached. Giving up.")
                    raise
                
                # Log the error and retry
                logger.warning(f"{error_msg}. Retrying in {self.retry_interval} seconds... (attempt {retry_count})")
                
                # Clean up failed connection attempt
                try:
                    await self.exit_stack.aclose()
                    self.exit_stack = AsyncExitStack()
                except Exception:
                    pass
                
                # Wait before retrying
                await asyncio.sleep(self.retry_interval)

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools from the MCP server."""
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")
            
        tools_response = await self.session.list_tools()
        return [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            }
        } for tool in tools_response.tools]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> tuple[Any, Dict[str, str]]:
        """
        Call a specific tool with given arguments.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments (without Authorization, as it goes in headers)
            extra_headers: Additional HTTP headers to include in the request (e.g., Authorization)
        
        Returns:
            A tuple of (result, headers) where headers is a dict containing response headers
        """
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")
        
        logger.info(f"Calling tool: {tool_name}")
        logger.debug(f"Tool arguments: {json.dumps(arguments, indent=2)}")
        if extra_headers:
            # Log headers but mask sensitive Authorization values
            safe_headers = {k: ('Bearer ***' if k == 'Authorization' and v.startswith('Bearer ') else v) 
                          for k, v in extra_headers.items()}
            logger.debug(f"Extra headers: {json.dumps(safe_headers, indent=2)}")
        
        headers = {}
        result = None
        
        # Make direct HTTP call to capture response headers
        if HAS_HTTPX:
            try:
                # Ensure no trailing slash on the URL
                mcp_url = self.mcp_http_url.rstrip('/')
                
                async with httpx.AsyncClient() as client:
                    # Construct MCP JSON-RPC request
                    mcp_request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": tool_name,
                            "arguments": arguments
                        }
                    }
                    
                    # Prepare request headers
                    request_headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"
                    }
                    
                    # Add extra headers if provided (e.g., Authorization)
                    if extra_headers:
                        request_headers.update(extra_headers)
                    
                    response = await client.post(
                        mcp_url,
                        json=mcp_request,
                        headers=request_headers,
                        timeout=30.0
                    )
                    
                    # Capture response headers
                    headers = dict(response.headers)
                    
                    # Log response details
                    logger.info(f"Tool response - HTTP Status: {response.status_code}")
                    
                    # Log relevant headers (filter out common noise)
                    relevant_headers = {k: v for k, v in headers.items() 
                                      if k.lower() in ['content-type', 'x-gravitee-endpoint-status', 
                                                       'x-gravitee-request-id', 'x-gravitee-transaction-id']}
                    if relevant_headers:
                        logger.info(f"Response headers: {json.dumps(relevant_headers, indent=2)}")
                    
                    # Try to parse response body
                    try:
                        response_text = response.text
                        
                        if response.status_code == 200:
                            response_data = response.json()
                            
                            if "error" in response_data:
                                error_msg = response_data["error"].get("message", "Unknown error")
                                logger.error(f"MCP returned error: {error_msg}")
                                raise RuntimeError(f"MCP tool call failed: {error_msg}")
                            
                            result = response_data.get("result", {})
                            
                            # Log the result payload
                            logger.info(f"Tool result payload: {json.dumps(result, indent=2)}")
                            
                            return result, headers
                        else:
                            logger.warning(f"HTTP call returned {response.status_code}: {response_text}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON response: {e}")
                    
            except Exception as e:
                logger.error(f"Direct HTTP call failed: {e}", exc_info=True)
                logger.info("Falling back to MCP session")
        
        # Fallback to using MCP session (won't have headers)
        if result is None:
            result = await self.session.call_tool(tool_name, arguments)
            logger.info(f"Tool result from MCP session: {json.dumps(result, indent=2)}")
        
        return result, headers

    async def cleanup(self):
        """Clean up resources."""
        try:
            await self.exit_stack.aclose()
            self.is_connected = False
            logger.info("MCP client cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

async def main_async():
    """Main async function for MCP Client."""
    client = MCPClient()
    try:
        await client.connect()
        
        # List available tools
        tools = await client.list_tools()
        print("Available MCP Tools:")
        for tool in tools:
            function_info = tool.get("function", {})
            print(f"- {function_info.get('name')}: {function_info.get('description')}")
        
        print("\nMCP Client ready. You can now call tools programmatically.")
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        await client.cleanup()
    
    return 0

def main():
    """Main entry point for MCP Client."""
    return asyncio.run(main_async())

if __name__ == "__main__":
    sys.exit(main())
