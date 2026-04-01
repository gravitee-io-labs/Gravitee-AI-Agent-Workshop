"""
MCP Client with Elicitation support.

This MCP client connects to MCP servers and supports:
- Tool discovery and execution
- Form Mode elicitation (inline data collection)
- URL Mode elicitation (out-of-band authentication flows)
"""

import asyncio
import os
import sys
import logging
import json
import uuid
from typing import Optional, Dict, Any, List, Callable, Awaitable
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client as mcp_http_client
from mcp.types import ElicitRequestParams, ElicitResult
from mcp.shared.context import RequestContext
from dotenv import load_dotenv

# Add the agent-server package to Python path to access colored_logger
sys.path.insert(0, '/app/hotel-booking-a2a-agent/agent-server')

try:
    from agent_server.colored_logger import get_mcp_logger
    logger = get_mcp_logger(__name__)
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

load_dotenv()

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

MCP_HTTP_URLS_DEFAULT = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", "http://gio-apim-gateway:8082/hotels/mcp"))
MCP_RETRY_INTERVAL = int(os.getenv("MCP_RETRY_INTERVAL", "5"))

# Type for the elicitation callback that the agent provides
ElicitationCallbackT = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class MCPClient:
    """MCP Client with Elicitation support."""

    def __init__(
        self,
        mcp_url: Optional[str] = None,
        retry_interval: int = MCP_RETRY_INTERVAL,
        elicitation_callback: Optional[ElicitationCallbackT] = None,
    ):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.mcp_http_url: str = mcp_url or MCP_HTTP_URLS_DEFAULT
        self.retry_interval: int = retry_interval
        self.is_connected: bool = False
        self.last_response_headers: Dict[str, str] = {}
        self._elicitation_callback = elicitation_callback

    def _create_sdk_elicitation_callback(self):
        """Create an MCP SDK-compatible elicitation callback.

        The SDK callback signature is:
            (context: RequestContext, params: ElicitRequestParams) -> ElicitResult | ErrorData
        """
        outer_callback = self._elicitation_callback

        async def sdk_elicitation_handler(
            context: RequestContext,
            params: ElicitRequestParams,
        ) -> ElicitResult:
            logger.info(f"Elicitation request received: {params.message[:80]}...")

            # Extract all params including extra fields (mode, url, elicitationId)
            params_dict = params.model_dump(exclude_none=True)
            mode = params_dict.get("mode", "form")

            elicitation_data = {
                "mode": mode,
                "message": params.message,
            }

            if mode == "form":
                elicitation_data["requestedSchema"] = params.requestedSchema
            elif mode == "url":
                elicitation_data["url"] = params_dict.get("url", "")
                elicitation_data["elicitationId"] = params_dict.get("elicitationId", str(uuid.uuid4()))

            logger.info(f"Elicitation mode: {mode}")

            if outer_callback:
                try:
                    response = await outer_callback(elicitation_data)
                    action = response.get("action", "cancel")
                    content = response.get("content")
                    logger.info(f"Elicitation response: action={action}")
                    return ElicitResult(action=action, content=content)
                except Exception as e:
                    logger.error(f"Elicitation callback error: {e}")
                    return ElicitResult(action="cancel", content=None)
            else:
                logger.warning("No elicitation callback registered — declining")
                return ElicitResult(action="decline", content=None)

        return sdk_elicitation_handler

    async def connect(self, url: Optional[str] = None, max_retries: Optional[int] = None):
        """Connect to MCP server with elicitation support."""
        if url:
            self.mcp_http_url = url

        retry_count = 0
        while True:
            try:
                http_transport = await self.exit_stack.enter_async_context(
                    mcp_http_client(self.mcp_http_url)
                )
                try:
                    read_stream, write_stream, _ = http_transport
                except Exception:
                    read_stream, write_stream = http_transport

                # Create session with elicitation callback
                elicitation_cb = self._create_sdk_elicitation_callback() if self._elicitation_callback else None

                self.session = await self.exit_stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        elicitation_callback=elicitation_cb,
                    )
                )
                await self.session.initialize()

                self.is_connected = True
                logger.info(f"Connected to MCP Server at {self.mcp_http_url}")
                break

            except Exception as e:
                retry_count += 1
                if max_retries is not None and retry_count >= max_retries:
                    logger.error(f"Failed to connect to {self.mcp_http_url}. Max retries ({max_retries}) reached.")
                    raise
                logger.warning(f"Failed to connect to {self.mcp_http_url}: {e}. Retrying in {self.retry_interval}s... (attempt {retry_count})")
                try:
                    await self.exit_stack.aclose()
                    self.exit_stack = AsyncExitStack()
                except Exception:
                    pass
                await asyncio.sleep(self.retry_interval)

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools from the MCP server."""
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server.")

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
        """Call a tool. During execution, elicitation callbacks may fire."""
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server.")

        logger.info(f"Calling tool: {tool_name}")
        logger.debug(f"Tool arguments: {json.dumps(arguments, indent=2)}")

        headers = {}
        result = None

        # Use direct HTTP for extra headers support (e.g., Authorization)
        if HAS_HTTPX and extra_headers:
            try:
                mcp_url = self.mcp_http_url.rstrip('/')
                async with httpx.AsyncClient() as client:
                    mcp_request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments}
                    }
                    request_headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"
                    }
                    request_headers.update(extra_headers)

                    response = await client.post(mcp_url, json=mcp_request, headers=request_headers, timeout=30.0)
                    headers = dict(response.headers)
                    logger.info(f"Tool response - HTTP Status: {response.status_code}")

                    if response.status_code == 200:
                        response_data = response.json()
                        if "error" in response_data:
                            error_msg = response_data["error"].get("message", "Unknown error")
                            raise RuntimeError(f"MCP tool call failed: {error_msg}")
                        result = response_data.get("result", {})
                        logger.info(f"Tool result: {json.dumps(result, indent=2)[:500]}")
                        return result, headers

            except Exception as e:
                logger.error(f"Direct HTTP call failed: {e}", exc_info=True)
                logger.info("Falling back to MCP session")

        # Use MCP session (supports elicitation callbacks)
        if result is None:
            result = await self.session.call_tool(tool_name, arguments)
            # Convert to dict for consistency
            result_dict = {
                "content": [
                    {"type": c.type, "text": c.text if hasattr(c, 'text') else str(c)}
                    for c in result.content
                ] if result.content else [],
                "isError": result.isError if hasattr(result, 'isError') else False,
            }
            logger.info(f"Tool result from session: {json.dumps(result_dict, indent=2)[:500]}")
            return result_dict, headers

        return result, headers

    async def cleanup(self):
        """Clean up resources."""
        try:
            await self.exit_stack.aclose()
            self.is_connected = False
            logger.info("MCP client cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


class MCPMultiClient:
    """Manager for multiple MCP clients with elicitation support."""

    def __init__(
        self,
        mcp_urls: Optional[List[str] | str] = None,
        retry_interval: int = MCP_RETRY_INTERVAL,
        elicitation_callback: Optional[ElicitationCallbackT] = None,
    ):
        if mcp_urls is None:
            mcp_urls_str = MCP_HTTP_URLS_DEFAULT
            self.mcp_urls = [url.strip() for url in mcp_urls_str.split(',')]
        elif isinstance(mcp_urls, str):
            self.mcp_urls = [url.strip() for url in mcp_urls.split(',')]
        else:
            self.mcp_urls = mcp_urls

        self.clients: Dict[str, MCPClient] = {}
        self.retry_interval = retry_interval
        self._elicitation_callback = elicitation_callback
        logger.info(f"Initialized MCPMultiClient with {len(self.mcp_urls)} server(s)")

    async def connect_all(self, max_retries: Optional[int] = None, connection_timeout: int = 30):
        """Connect to all MCP servers."""
        for url in self.mcp_urls:
            client = MCPClient(
                mcp_url=url,
                retry_interval=self.retry_interval,
                elicitation_callback=self._elicitation_callback,
            )
            try:
                await asyncio.wait_for(
                    client.connect(max_retries=max_retries),
                    timeout=connection_timeout
                )
                self.clients[url] = client
                logger.info(f"Connected to MCP server: {url}")
            except asyncio.TimeoutError:
                logger.error(f"Connection to {url} timed out after {connection_timeout}s")
                try:
                    await client.cleanup()
                except Exception:
                    pass
            except asyncio.CancelledError:
                logger.warning(f"Connection to {url} was cancelled")
                try:
                    await client.cleanup()
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Failed to connect to {url}: {e}")
                try:
                    await client.cleanup()
                except Exception:
                    pass

        if not self.clients:
            raise RuntimeError("Failed to connect to any MCP servers")

        logger.info(f"Connected to {len(self.clients)}/{len(self.mcp_urls)} MCP server(s)")

    async def list_all_tools(self) -> List[Dict[str, Any]]:
        """List all available tools from all connected MCP servers."""
        all_tools = []
        for url, client in self.clients.items():
            try:
                tools = await client.list_tools()
                all_tools.extend(tools)
            except Exception as e:
                logger.error(f"Failed to list tools from {url}: {e}")
        logger.info(f"Total tools: {len(all_tools)}")
        return all_tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> tuple[Any, Dict[str, str]]:
        """Call a tool on the first server that succeeds.

        A server returning ``isError: true`` with an "Unknown tool" message
        is treated as a miss and the next server is tried.
        """
        last_error = None
        for url, client in self.clients.items():
            try:
                result, headers = await client.call_tool(tool_name, arguments, extra_headers)
                # Check if the server says "Unknown tool" — try next server
                if isinstance(result, dict) and result.get("isError"):
                    content = result.get("content", [])
                    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                    full_text = " ".join(texts)
                    if "unknown tool" in full_text.lower():
                        logger.debug(f"Tool {tool_name} not found on {url}, trying next server")
                        last_error = RuntimeError(full_text)
                        continue
                return result, headers
            except Exception as e:
                logger.debug(f"Tool {tool_name} failed on {url}: {e}")
                last_error = e
                continue

        raise RuntimeError(f"Tool {tool_name} failed on all servers. Last error: {last_error}")

    async def cleanup(self):
        """Clean up all clients."""
        for url, client in self.clients.items():
            try:
                await client.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up {url}: {e}")
        self.clients.clear()


async def main_async():
    client = MCPClient()
    try:
        await client.connect()
        tools = await client.list_tools()
        print("Available MCP Tools:")
        for tool in tools:
            fn = tool.get("function", {})
            print(f"- {fn.get('name')}: {fn.get('description')}")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        await client.cleanup()
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
