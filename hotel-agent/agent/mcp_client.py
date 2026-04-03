"""MCP Client with elicitation support."""
import asyncio
import json
import logging
import os
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client as mcp_http_client
from mcp.types import ElicitRequestParams, ElicitResult
from mcp.shared.context import RequestContext
from dotenv import load_dotenv

from agent.logger import get_mcp_logger

load_dotenv()
logger = get_mcp_logger(__name__)

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

MCP_HTTP_URLS_DEFAULT = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", ""))
MCP_RETRY_INTERVAL = int(os.getenv("MCP_RETRY_INTERVAL", "5"))

ElicitationCallbackT = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class MCPClient:
    """Single MCP server connection with elicitation support."""

    def __init__(self, mcp_url: str, retry_interval: int = MCP_RETRY_INTERVAL,
                 elicitation_callback: Optional[ElicitationCallbackT] = None):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.mcp_http_url = mcp_url
        self.retry_interval = retry_interval
        self.is_connected = False
        self._elicitation_callback = elicitation_callback

    def _create_sdk_elicitation_callback(self):
        outer_callback = self._elicitation_callback

        async def handler(context: RequestContext, params: ElicitRequestParams) -> ElicitResult:
            logger.info(f"Elicitation received: {params.message[:80]}...")
            params_dict = params.model_dump(exclude_none=True)
            mode = params_dict.get("mode", "form")

            elicitation_data: Dict[str, Any] = {"mode": mode, "message": params.message}
            if mode == "form":
                elicitation_data["requestedSchema"] = params.requestedSchema
            elif mode == "url":
                elicitation_data["url"] = params_dict.get("url", "")
                elicitation_data["elicitationId"] = params_dict.get("elicitationId", str(uuid.uuid4()))

            if outer_callback:
                try:
                    response = await outer_callback(elicitation_data)
                    return ElicitResult(action=response.get("action", "cancel"), content=response.get("content"))
                except Exception as e:
                    logger.error(f"Elicitation callback error: {e}")
                    return ElicitResult(action="cancel", content=None)
            return ElicitResult(action="decline", content=None)

        return handler

    async def connect(self, max_retries: Optional[int] = None):
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

                elicitation_cb = self._create_sdk_elicitation_callback() if self._elicitation_callback else None
                self.session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream, elicitation_callback=elicitation_cb)
                )
                await self.session.initialize()
                self.is_connected = True
                logger.info(f"Connected to {self.mcp_http_url}")
                break
            except Exception as e:
                retry_count += 1
                if max_retries is not None and retry_count >= max_retries:
                    logger.error(f"Failed to connect to {self.mcp_http_url} after {max_retries} attempts")
                    raise
                logger.warning(f"Connection to {self.mcp_http_url} failed: {e}. Retrying in {self.retry_interval}s...")
                try:
                    await self.exit_stack.aclose()
                    self.exit_stack = AsyncExitStack()
                except Exception:
                    pass
                await asyncio.sleep(self.retry_interval)

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server")
        tools_response = await self.session.list_tools()
        return [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            },
        } for tool in tools_response.tools]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any],
                        extra_headers: Optional[Dict[str, str]] = None) -> tuple[Any, Dict[str, str]]:
        if not self.session or not self.is_connected:
            raise RuntimeError("Not connected to MCP server")

        logger.info(f"Calling tool: {tool_name}")

        # Use direct HTTP when extra headers are needed (e.g. Authorization)
        if HAS_HTTPX and extra_headers:
            try:
                async with httpx.AsyncClient() as client:
                    mcp_request = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    }
                    request_headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    }
                    request_headers.update(extra_headers)
                    response = await client.post(
                        self.mcp_http_url.rstrip('/'), json=mcp_request,
                        headers=request_headers, timeout=30.0,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if "error" in data:
                            raise RuntimeError(data["error"].get("message", "Unknown error"))
                        return data.get("result", {}), dict(response.headers)
            except Exception as e:
                logger.warning(f"Direct HTTP failed, falling back to session: {e}")

        # MCP session path (supports elicitation callbacks)
        result = await self.session.call_tool(tool_name, arguments)
        result_dict = {
            "content": [
                {"type": c.type, "text": c.text if hasattr(c, 'text') else str(c)}
                for c in result.content
            ] if result.content else [],
            "isError": result.isError if hasattr(result, 'isError') else False,
        }
        return result_dict, {}

    async def cleanup(self):
        try:
            await self.exit_stack.aclose()
            self.is_connected = False
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


class MCPMultiClient:
    """Manages multiple MCP server connections."""

    def __init__(self, mcp_urls: Optional[List[str] | str] = None,
                 retry_interval: int = MCP_RETRY_INTERVAL,
                 elicitation_callback: Optional[ElicitationCallbackT] = None):
        if mcp_urls is None or mcp_urls == "":
            urls_str = MCP_HTTP_URLS_DEFAULT
        elif isinstance(mcp_urls, str):
            urls_str = mcp_urls
        else:
            urls_str = None

        self.mcp_urls = [u.strip() for u in urls_str.split(',')] if urls_str else (mcp_urls if isinstance(mcp_urls, list) else [])
        self.clients: Dict[str, MCPClient] = {}
        self.retry_interval = retry_interval
        self._elicitation_callback = elicitation_callback
        logger.info(f"MCPMultiClient: {len(self.mcp_urls)} server(s)")

    async def connect_all(self, max_retries: Optional[int] = None, connection_timeout: int = 30):
        for url in self.mcp_urls:
            client = MCPClient(url, self.retry_interval, self._elicitation_callback)
            try:
                await asyncio.wait_for(client.connect(max_retries=max_retries), timeout=connection_timeout)
                self.clients[url] = client
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
                logger.error(f"Failed to connect to {url}: {e}")
                try:
                    await client.cleanup()
                except Exception:
                    pass

        if not self.clients:
            raise RuntimeError("Failed to connect to any MCP servers")
        logger.info(f"Connected to {len(self.clients)}/{len(self.mcp_urls)} MCP server(s)")

    async def list_all_tools(self) -> List[Dict[str, Any]]:
        all_tools = []
        for url, client in self.clients.items():
            try:
                all_tools.extend(await client.list_tools())
            except Exception as e:
                logger.error(f"Failed to list tools from {url}: {e}")
        return all_tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any],
                        extra_headers: Optional[Dict[str, str]] = None) -> tuple[Any, Dict[str, str]]:
        last_error = None
        for url, client in self.clients.items():
            try:
                result, headers = await client.call_tool(tool_name, arguments, extra_headers)
                if isinstance(result, dict) and result.get("isError"):
                    content = result.get("content", [])
                    texts = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    if "unknown tool" in texts.lower():
                        last_error = RuntimeError(texts)
                        continue
                return result, headers
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"Tool {tool_name} failed on all servers: {last_error}")

    async def cleanup(self):
        for client in self.clients.values():
            try:
                await client.cleanup()
            except Exception:
                pass
        self.clients.clear()
