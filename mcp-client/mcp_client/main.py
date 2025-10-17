import asyncio
import os
import sys
import logging
from typing import Optional, Dict, Any, List
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client as mcp_http_client
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MCP_HTTP_URL_DEFAULT = os.getenv("MCP_HTTP_URL", "http://localhost:8082/bookings/mcp")
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
                logger.info(f"Attempting to connect to MCP server at {self.mcp_http_url}...")
                
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
                logger.info(f"Successfully connected to MCP Server at {self.mcp_http_url}")
                print(f"Connected to MCP Server at {self.mcp_http_url}")
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

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a specific tool with given arguments."""
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")
            
        return await self.session.call_tool(tool_name, arguments)

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
