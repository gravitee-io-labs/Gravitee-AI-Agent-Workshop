import asyncio
import os
import sys
from typing import Optional, Dict, Any, List
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client as mcp_http_client
from dotenv import load_dotenv

load_dotenv()

MCP_HTTP_URL_DEFAULT = os.getenv("MCP_HTTP_URL", "http://localhost:8082/bookings/mcp")

class MCPClient:
    """MCP Client for discovering and calling tools from MCP servers."""
    
    def __init__(self, mcp_url: Optional[str] = None):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.transport_mode: str = "http"
        self.mcp_http_url: str = mcp_url or MCP_HTTP_URL_DEFAULT

    async def connect(self, url: Optional[str] = None):
        """Connect to MCP server using HTTP transport."""
        if url:
            self.mcp_http_url = url
            
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
        print(f"Connected to MCP Server at {self.mcp_http_url}")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools from the MCP server."""
        if not self.session:
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
        if not self.session:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")
            
        return await self.session.call_tool(tool_name, arguments)

    async def cleanup(self):
        """Clean up resources."""
        await self.exit_stack.aclose()

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
