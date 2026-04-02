"""
Generic A2A Agent — discovers MCP tools and lets the LLM decide.

No hardcoded business logic. The agent:
  1. Connects to MCP servers and discovers available tools
  2. Forwards user messages + tool definitions to the LLM
  3. Executes whichever tool the LLM picks
  4. Returns the LLM-formatted result

Elicitation (form / URL mode) is supported transparently.
"""

import os
import uuid
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager
from collections import OrderedDict

from dotenv import load_dotenv

from agent.mcp_client import MCPMultiClient
from agent.llm_client import LLMClient, LLMRateLimitError
from agent.auth_service import AuthService, AuthenticationError
from agent.logger import get_agent_logger

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard, AgentSkill, AgentCapabilities,
    Message, Role, TextPart, DataPart,
)

load_dotenv()

# ---------------------------------------------------------------------------
#  Configuration (all from environment)
# ---------------------------------------------------------------------------
AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
AGENT_NAME = os.getenv("AGENT_NAME", "MCP Agent")
AGENT_DESCRIPTION = os.getenv("AGENT_DESCRIPTION", "AI agent that discovers and uses MCP tools")

MCP_HTTP_URLS = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", ""))
OIDC_DISCOVERY_URL = os.getenv("OIDC_DISCOVERY_URL", "")
AM_TOKEN_URL = os.getenv("AM_TOKEN_URL", "")
AM_CLIENT_ID = os.getenv("AM_CLIENT_ID", "")
AM_CLIENT_SECRET = os.getenv("AM_CLIENT_SECRET", "")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful AI assistant. Use the available tools when they match the user's intent. "
    "Whenever possible, personalize your responses using the user's first name.",
)

logger = get_agent_logger(__name__)


# ---------------------------------------------------------------------------
#  Conversation Store
# ---------------------------------------------------------------------------
MAX_CONVERSATIONS = 200
MAX_HISTORY_MESSAGES = 40
CONVERSATION_TTL_SECS = 3600


class ConversationStore:
    """In-memory conversation history keyed by context ID."""

    def __init__(self):
        self._store: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        self._ts: Dict[str, float] = {}

    def get(self, cid: str) -> List[Dict[str, Any]]:
        self._evict()
        self._ts[cid] = time.time()
        return list(self._store.get(cid, []))

    def add(self, cid: str, role: str, text: str):
        if cid not in self._store:
            self._store[cid] = []
            while len(self._store) > MAX_CONVERSATIONS:
                k, _ = self._store.popitem(last=False)
                self._ts.pop(k, None)
        self._store[cid].append({"role": role, "content": text})
        if len(self._store[cid]) > MAX_HISTORY_MESSAGES:
            self._store[cid] = self._store[cid][-MAX_HISTORY_MESSAGES:]
        self._ts[cid] = time.time()

    def _evict(self):
        now = time.time()
        for k in [k for k, ts in self._ts.items() if now - ts > CONVERSATION_TTL_SECS]:
            self._store.pop(k, None)
            self._ts.pop(k, None)


conversations = ConversationStore()


# ---------------------------------------------------------------------------
#  Elicitation Manager  (async bridge: MCP callback <-> A2A request/response)
# ---------------------------------------------------------------------------
class ElicitationManager:

    def __init__(self):
        self.pending_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._futures: Dict[str, asyncio.Future] = {}

    async def request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        eid = data.setdefault("elicitationId", str(uuid.uuid4()))
        future: asyncio.Future[Dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._futures[eid] = future
        await self.pending_queue.put(data)
        return await future

    def resolve(self, eid: str, response: Dict[str, Any]):
        future = self._futures.pop(eid, None)
        if future and not future.done():
            future.set_result(response)


elicitation_mgr = ElicitationManager()


# ---------------------------------------------------------------------------
#  Generic MCP Agent
# ---------------------------------------------------------------------------
class MCPAgent:

    def __init__(self):
        self.mcp = MCPMultiClient(
            mcp_urls=MCP_HTTP_URLS,
            elicitation_callback=elicitation_mgr.request,
        )
        self.llm = LLMClient()
        self.auth = AuthService(
            oidc_discovery_url=OIDC_DISCOVERY_URL,
            am_token_url=AM_TOKEN_URL,
            am_client_id=AM_CLIENT_ID,
            am_client_secret=AM_CLIENT_SECRET,
        )
        self._ready = False

    async def initialize(self):
        await self.mcp.connect_all(max_retries=3, connection_timeout=15)
        if OIDC_DISCOVERY_URL:
            await self.auth.initialize()
        self._ready = True
        logger.info("Agent initialized")

    async def process(
        self,
        message: str,
        authorization: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Process a user message: discover tools, call LLM, execute tool, return result."""
        tools = await self.mcp.list_all_tools()
        logger.info(f"Available tools: {len(tools)}")

        content, tool_calls = await self.llm.process_query(
            message, tools,
            system_prompt=SYSTEM_PROMPT,
            conversation_history=history,
        )

        # No tool selected — return the LLM's direct response
        if not tool_calls:
            return content or "I couldn't determine how to help. Could you provide more details?"

        # Execute the first tool call
        tc = tool_calls[0]
        tool_name = tc["function"]["name"]
        tool_args = tc["function"]["arguments"]
        logger.info(f"Executing: {tool_name}({json.dumps(tool_args)[:200]})")

        result, _ = await self.mcp.call_tool(tool_name, tool_args)

        # Retry with auth if tool returned an error
        if self._is_error(result):
            if authorization:
                try:
                    token, email = await self.auth.process_authorization_for_tool(authorization)
                    result, _ = await self.mcp.call_tool(
                        tool_name, tool_args,
                        extra_headers={"Authorization": f"Bearer {token}", "sub-email": email},
                    )
                except AuthenticationError:
                    return "Authentication required. Please sign in and try again."
            else:
                return "Authentication required. Please sign in and try again."

        # Let the LLM format the tool result into a human-readable response
        try:
            return await self.llm.process_tool_result(
                message, tc, result,
                system_prompt=SYSTEM_PROMPT,
                conversation_history=history,
            )
        except LLMRateLimitError:
            return self._extract_text(result)

    async def cleanup(self):
        await self.mcp.cleanup()
        await self.auth.cleanup()

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _is_error(result: Any) -> bool:
        if isinstance(result, dict):
            return result.get("isError", False)
        return getattr(result, "isError", False)

    @staticmethod
    def _extract_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            contents = result.get("content", [])
            if isinstance(contents, list):
                texts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text"]
                if texts:
                    return "\n".join(texts)
        return str(result)


# ---------------------------------------------------------------------------
#  A2A Request Handler
# ---------------------------------------------------------------------------
_pending_tasks: Dict[str, asyncio.Task] = {}


class AgentRequestHandler(RequestHandler):

    def __init__(self):
        self.agent = MCPAgent()
        super().__init__()

    async def on_message_send(self, params, context):
        try:
            if not self.agent._ready:
                await self.agent.initialize()

            authorization = self._get_authorization(context)
            context_id = self._get_context_id(params)

            # Check for elicitation response first
            elicitation_resp = self._get_elicitation_response(params)
            if elicitation_resp:
                eid = elicitation_resp.get("elicitationId")
                content_data = elicitation_resp.get("content", {})
                if content_data:
                    summary = ", ".join(f"{k}: {v}" for k, v in content_data.items())
                    conversations.add(context_id, "user", f"[Form response] {summary}")

                elicitation_mgr.resolve(eid, elicitation_resp)

                task = _pending_tasks.pop(eid, None)
                if task:
                    try:
                        response_text = await asyncio.wait_for(task, timeout=120)
                    except asyncio.TimeoutError:
                        response_text = "The request timed out."
                else:
                    response_text = "Thank you for providing the information."

                conversations.add(context_id, "assistant", response_text)
                return self._message(response_text)

            # Normal user message
            user_text = self._get_text(params)
            if not user_text:
                raise ValueError("No message content provided.")

            conversations.add(context_id, "user", user_text)
            history = conversations.get(context_id)

            # Start processing (may trigger elicitation)
            tool_task = asyncio.create_task(
                self.agent.process(user_text, authorization, history)
            )
            elicitation_wait = asyncio.create_task(elicitation_mgr.pending_queue.get())

            done, _ = await asyncio.wait(
                [tool_task, elicitation_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if elicitation_wait in done:
                # Elicitation requested — relay to frontend
                elicitation_data = elicitation_wait.result()
                eid = elicitation_data["elicitationId"]
                _pending_tasks[eid] = tool_task

                msg = elicitation_data.get("message", "Please provide the requested information.")
                conversations.add(context_id, "assistant", f"[Elicitation: {elicitation_data.get('mode', 'form')}] {msg}")

                return Message(
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[
                        DataPart(data=elicitation_data, metadata={"type": "elicitation"}),
                        TextPart(text=msg),
                    ],
                )
            else:
                elicitation_wait.cancel()
                response_text = tool_task.result()
                conversations.add(context_id, "assistant", response_text)
                return self._message(response_text)

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            return self._message(f"Sorry, I encountered an error: {e}")

    async def on_message_send_stream(self, params, context):
        yield await self.on_message_send(params, context)

    # -- A2A stubs (not used) ----------------------------------------------- #
    async def on_create_task(self, params, context=None):
        raise NotImplementedError
    async def on_get_task(self, params, context=None):
        raise NotImplementedError
    async def on_cancel_task(self, params, context=None):
        raise NotImplementedError
    async def on_list_tasks(self, params, context=None):
        return []
    async def on_set_task_push_notification_config(self, params, context=None):
        raise NotImplementedError
    async def on_get_task_push_notification_config(self, params, context=None):
        raise NotImplementedError
    async def on_resubscribe_to_task(self, params, context=None):
        raise NotImplementedError
    async def on_list_task_push_notification_config(self, params, context=None):
        return []
    async def on_delete_task_push_notification_config(self, params, context=None):
        return None

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _message(text: str) -> Message:
        return Message(
            messageId=str(uuid.uuid4()),
            role=Role.agent,
            parts=[TextPart(text=text)],
        )

    @staticmethod
    def _get_authorization(context) -> Optional[str]:
        if context and hasattr(context, 'state'):
            state = context.state
            if isinstance(state, dict) and 'headers' in state:
                h = state['headers']
                return h.get('authorization') or h.get('Authorization')
        if context and hasattr(context, 'http_request'):
            req = context.http_request
            if hasattr(req, 'headers'):
                return req.headers.get('Authorization') or req.headers.get('authorization')
        return None

    @staticmethod
    def _get_context_id(params) -> str:
        msg = params.message
        for attr in ('context_id', 'contextId'):
            val = getattr(msg, attr, None)
            if val:
                return str(val)
        mid = getattr(msg, 'message_id', None) or getattr(msg, 'messageId', None)
        return str(mid) if mid else str(uuid.uuid4())

    @staticmethod
    def _unwrap(part):
        return part.root if hasattr(part, 'root') else part

    @staticmethod
    def _get_text(params) -> str:
        for raw in (params.message.parts or []):
            part = AgentRequestHandler._unwrap(raw)
            if hasattr(part, 'text') and part.text:
                return part.text
            if isinstance(part, dict) and 'text' in part:
                return part['text']
        return ""

    @staticmethod
    def _get_elicitation_response(params) -> Optional[Dict[str, Any]]:
        for raw in (params.message.parts or []):
            part = AgentRequestHandler._unwrap(raw)
            if hasattr(part, 'data') and hasattr(part, 'metadata'):
                meta = part.metadata or {}
                if isinstance(meta, dict) and meta.get("type") == "elicitation_response":
                    return part.data
            if isinstance(part, dict):
                meta = part.get("metadata", {})
                if isinstance(meta, dict) and meta.get("type") == "elicitation_response":
                    return part.get("data", {})
        return None


# ---------------------------------------------------------------------------
#  Application
# ---------------------------------------------------------------------------
def create_agent_card() -> AgentCard:
    return AgentCard(
        name=AGENT_NAME,
        version="1.0.0",
        description=AGENT_DESCRIPTION,
        url=f"http://localhost:{AGENT_SERVER_PORT}",
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False, stateTransitionHistory=True),
        skills=[AgentSkill(
            id="mcp-tools",
            name="mcp-tools",
            description="Discovers and uses MCP tools to fulfill user requests",
            tags=["mcp", "tools"],
        )],
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        protocolVersion="0.3.0",
        preferredTransport="JSONRPC",
    )


_agent_instance: Optional[MCPAgent] = None


def create_app():
    handler = AgentRequestHandler()
    app = A2AStarletteApplication(agent_card=create_agent_card(), http_handler=handler)
    return app.build(), handler.agent


def main():
    logging.getLogger().setLevel(logging.INFO)
    app, agent = create_app()

    @asynccontextmanager
    async def lifespan(app):
        global _agent_instance
        _agent_instance = agent
        await agent.initialize()
        logger.info("Agent ready")
        yield
        await agent.cleanup()
        logger.info("Agent stopped")

    app.router.lifespan_context = lifespan

    import uvicorn
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    uvicorn.run(app, host="0.0.0.0", port=AGENT_SERVER_PORT, log_level="info", log_config=log_config)


if __name__ == "__main__":
    main()
