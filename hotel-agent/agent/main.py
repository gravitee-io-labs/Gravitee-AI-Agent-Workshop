"""ACME Hotel Agent — A2A 1.0 + MCP + RFC 8693 Token Exchange."""

import os
import uuid
import asyncio
import json
import logging
import time
from typing import Any
from contextlib import asynccontextmanager
from collections import OrderedDict

from dotenv import load_dotenv
from google.protobuf import struct_pb2

from agent.mcp_client import MCPMultiClient
from agent.llm_client import LLMClient, LLMRateLimitError, LLMRequestBlockedError
from agent.auth_service import AuthService, AuthenticationError
from agent.logger import get_agent_logger

from a2a.server.apps import A2AStarletteApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, AgentInterface, Part
from a2a.utils import new_agent_text_message, new_agent_parts_message

load_dotenv()

# --- Configuration ---

AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
AGENT_NAME = os.getenv("AGENT_NAME", "ACME Hotel Agent")
AGENT_DESCRIPTION = os.getenv(
    "AGENT_DESCRIPTION",
    "AI-powered hotel booking assistant. Searches hotels, manages reservations, "
    "and helps guests with all aspects of their stay.",
)
MCP_HTTP_URLS = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", ""))
AM_TOKEN_URL = os.getenv("AM_TOKEN_URL", "")
AM_CLIENT_ID = os.getenv("AM_CLIENT_ID", "")
AM_CLIENT_SECRET = os.getenv("AM_CLIENT_SECRET", "")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a Hotel booking assistant. "
    "Help guests search for hotels, check availability, make reservations, and manage their bookings. "
    "Use the available tools when they match the user's intent. "
    "Be friendly, concise, and whenever possible, personalize your responses using the guest's first name.",
)

logger = get_agent_logger(__name__)

MAX_CONVERSATIONS = 200
MAX_HISTORY_MESSAGES = 40
CONVERSATION_TTL_SECS = 3600


# --- Conversation Store ---

class ConversationStore:
    """In-memory, TTL-evicted conversation history (OpenAI message format)."""

    def __init__(self):
        self._store: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._ts: dict[str, float] = {}

    def get(self, cid: str) -> list[dict[str, Any]]:
        self._evict()
        self._ts[cid] = time.time()
        return list(self._store.get(cid, []))

    def add(self, cid: str, role: str, text: str):
        self._append(cid, {"role": role, "content": text})

    def add_raw(self, cid: str, messages: list[dict[str, Any]]):
        for msg in messages:
            self._append(cid, msg)

    def _append(self, cid: str, message: dict[str, Any]):
        if cid not in self._store:
            self._store[cid] = []
            while len(self._store) > MAX_CONVERSATIONS:
                k, _ = self._store.popitem(last=False)
                self._ts.pop(k, None)
        self._store[cid].append(message)
        if len(self._store[cid]) > MAX_HISTORY_MESSAGES:
            self._store[cid] = self._store[cid][-MAX_HISTORY_MESSAGES:]
        self._ts[cid] = time.time()

    def _evict(self):
        now = time.time()
        for k in [k for k, ts in self._ts.items() if now - ts > CONVERSATION_TTL_SECS]:
            self._store.pop(k, None)
            self._ts.pop(k, None)


conversations = ConversationStore()


# --- Elicitation Manager ---

class ElicitationManager:
    """Async bridge between MCP elicitation callbacks and A2A request/response."""

    def __init__(self):
        self.pending_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._futures: dict[str, asyncio.Future] = {}

    async def request(self, data: dict[str, Any]) -> dict[str, Any]:
        eid = data.setdefault("elicitationId", str(uuid.uuid4()))
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._futures[eid] = future
        await self.pending_queue.put(data)
        return await future

    def resolve(self, eid: str, response: dict[str, Any]):
        future = self._futures.pop(eid, None)
        if future and not future.done():
            future.set_result(response)


elicitation_mgr = ElicitationManager()


# --- MCP Agent (4-steps pipeline) ---

class MCPAgent:

    def __init__(self):
        self.mcp = MCPMultiClient(mcp_urls=MCP_HTTP_URLS, elicitation_callback=elicitation_mgr.request)
        self.llm = LLMClient()
        self.auth = AuthService(am_token_url=AM_TOKEN_URL, am_client_id=AM_CLIENT_ID, am_client_secret=AM_CLIENT_SECRET)
        self._ready = False

    async def initialize(self):
        await self.mcp.connect_all(max_retries=3, connection_timeout=15)
        if AM_TOKEN_URL:
            await self.auth.initialize()
        self._ready = True
        logger.info("Agent initialized")

    async def get_mcp_token(self, authorization: str | None) -> str | None:
        """Auth — Resolve the token to use for ALL MCP calls.

        - No AM configured → None (no auth)
        - User token present → RFC 8693 exchange (delegation), fallback to agent token
        - No user token → agent's own token (auto-refreshed if expired)
        """
        if not AM_TOKEN_URL:
            return None
        if authorization:
            try:
                delegated = await self.auth.process_authorization_for_tool(authorization)
                logger.info("Using delegated token (RFC 8693 exchange)")
                return delegated
            except AuthenticationError as e:
                logger.warning(f"Token exchange failed: {e} — falling back to agent token")
        return await self.auth.ensure_agent_token()

    async def process(
        self, message: str, token: str | None = None, history: list[dict] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        mcp_headers = {"Authorization": f"Bearer {token}"} if token else None

        # Step 1 — MCP Tools Discovery
        tools = await self.mcp.list_all_tools(extra_headers=mcp_headers)
        logger.info(f"Step 1 - MCP Tools Discovery: {len(tools)} tools availables.")
        
        # Step 2 — LLM decides which tool to call
        try:
            content, tool_calls = await self.llm.process_query(
                message, tools, system_prompt=SYSTEM_PROMPT, conversation_history=history,
            )
        except LLMRequestBlockedError:
            return "Your request was blocked because it was deemed invalid or unsafe.", []

        if not tool_calls:
            logger.info("Step 2 - LLM reasoning: LLM did not select any tool.")
            return (content or "I couldn't determine how to help. Could you provide more details?"), []
        else:
            logger.info(f"Step 2 - LLM reasoning: LLM selected {len(tool_calls)} tool(s): {', '.join([tc['function']['name'] for tc in tool_calls])}")
            
        # Step 3 — Execution of the selected tool (currently only supports the 1st one).
        tc = tool_calls[0]
        tool_name, tool_args = tc["function"]["name"], tc["function"]["arguments"]
        logger.info(f"Step 3 - Tool Execution: {tool_name}({json.dumps(tool_args)[:200]})")

        result, _ = await self.mcp.call_tool(tool_name, tool_args, extra_headers=mcp_headers)
        if self._is_error(result):
            logger.error(f"Step 3 - Tool Execution: {tool_name} failed with error: {self._extract_text(result)}")
            return f"The operation failed: {self._extract_text(result)}", []
        else:
            logger.info(f"Step 3 - Tool Execution: {tool_name} succeeded.")

        tool_messages = self._build_tool_messages(tc, tool_name, tool_args, result)

        # Step 4 — Reflect, LLM formats result for user, given the tool response
        try:
            response = await self.llm.process_tool_result(
                message, tc, result, system_prompt=SYSTEM_PROMPT,
            )
            logger.info(f"Step 4 - LLM formatting: successfully formatted the tool result for user response.")
            return response, tool_messages
        except LLMRequestBlockedError:
            logger.warning("Step 4 - LLM call failed because the response was blocked by safety filters.")
            return "Your request was blocked because it was deemed invalid or unsafe.", tool_messages
        except Exception as e:
            logger.error(f"Step 4 — LLM call failed ({type(e).__name__}: {e}), returning raw result")
            return self._extract_text(result), tool_messages

    async def cleanup(self):
        await self.mcp.cleanup()
        await self.auth.cleanup()

    @staticmethod
    def _is_error(result: Any) -> bool:
        return result.get("isError", False) if isinstance(result, dict) else getattr(result, "isError", False)

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

    @staticmethod
    def _build_tool_messages(tc: dict, name: str, args: dict, result: Any) -> list[dict]:
        call_id = tc.get("id", "call_0")
        result_str = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
        return [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            ]},
            {"role": "tool", "tool_call_id": call_id, "content": result_str},
        ]


# --- Protobuf helpers ---

def _to_value(d: dict) -> struct_pb2.Value:
    val = struct_pb2.Value()
    val.struct_value.update(d)
    return val


def _to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


# --- A2A Executor ---

_pending_tasks: dict[str, asyncio.Task] = {}


class HotelAgentExecutor(AgentExecutor):

    def __init__(self, agent: MCPAgent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            if not self.agent._ready:
                await self.agent.initialize()

            context_id = context.context_id or str(uuid.uuid4())
            logger.info("=" * 60)

            authorization = self._get_authorization(context)
            token = await self.agent.get_mcp_token(authorization)

            # Handle elicitation response
            elicitation_resp = self._get_elicitation_response(context)
            if elicitation_resp:
                response_text = await self._handle_elicitation(context_id, elicitation_resp)
                await self._reply(event_queue, response_text, context_id, context.task_id)
                return

            # Handle normal message
            user_text = context.get_user_input()
            if not user_text:
                await self._reply(event_queue, "No message content provided.", context_id, context.task_id)
                return

            logger.info(f"User prompt: {user_text[:150]}")
            conversations.add(context_id, "user", user_text)
            history = conversations.get(context_id)

            # Race: pipeline vs elicitation request
            tool_task = asyncio.create_task(self.agent.process(user_text, token, history))
            elicitation_wait = asyncio.create_task(elicitation_mgr.pending_queue.get())
            done, _ = await asyncio.wait({tool_task, elicitation_wait}, return_when=asyncio.FIRST_COMPLETED)

            if elicitation_wait in done:
                elicitation_data = elicitation_wait.result()
                eid = elicitation_data["elicitationId"]
                _pending_tasks[eid] = tool_task
                msg = elicitation_data.get("message", "Please provide the requested information.")
                conversations.add(context_id, "assistant", f"[Elicitation] {msg}")
                await event_queue.enqueue_event(new_agent_parts_message(
                    parts=[
                        Part(data=_to_value(elicitation_data), metadata=_to_struct({"type": "elicitation"})),
                        Part(text=msg),
                    ],
                    context_id=context_id, task_id=context.task_id,
                ))
            else:
                elicitation_wait.cancel()
                response_text, tool_msgs = tool_task.result()
                if tool_msgs:
                    conversations.add_raw(context_id, tool_msgs)
                conversations.add(context_id, "assistant", response_text)
                await self._reply(event_queue, response_text, context_id, context.task_id)

        except BaseException as e:
            logger.error(f"Error ({type(e).__name__}): {e}", exc_info=True)
            await self._reply(event_queue, "Sorry, I encountered an error. Please try again.",
                              context.context_id, context.task_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._reply(event_queue, "Cancellation is not supported.", context.context_id, context.task_id)

    # --- helpers ---

    @staticmethod
    async def _reply(eq: EventQueue, text: str, context_id: str | None, task_id: str | None):
        await eq.enqueue_event(new_agent_text_message(text, context_id=context_id, task_id=task_id))

    @staticmethod
    async def _handle_elicitation(context_id: str, resp: dict[str, Any]) -> str:
        eid = resp.get("elicitationId")
        content_data = resp.get("content", {})
        if content_data:
            summary = ", ".join(f"{k}: {v}" for k, v in content_data.items())
            conversations.add(context_id, "user", f"[Form response] {summary}")

        elicitation_mgr.resolve(eid, resp)

        task = _pending_tasks.pop(eid, None)
        if task:
            try:
                response_text, tool_msgs = await asyncio.wait_for(task, timeout=120)
                if tool_msgs:
                    conversations.add_raw(context_id, tool_msgs)
            except asyncio.TimeoutError:
                response_text = "The request timed out."
        else:
            response_text = "Thank you for providing the information."

        conversations.add(context_id, "assistant", response_text)
        return response_text

    @staticmethod
    def _get_authorization(context: RequestContext) -> str | None:
        if context.call_context:
            headers = context.call_context.state.get("headers", {})
            return headers.get("authorization") or headers.get("Authorization")
        return None

    @staticmethod
    def _get_elicitation_response(context: RequestContext) -> dict[str, Any] | None:
        if not context.message or not context.message.parts:
            return None
        for part in context.message.parts:
            if part.HasField("data") and part.metadata:
                if dict(part.metadata).get("type") == "elicitation_response":
                    return dict(part.data.struct_value)
        return None


# --- Agent Card ---

def create_agent_card() -> AgentCard:
    return AgentCard(
        name=AGENT_NAME,
        version="1.0.0",
        description=AGENT_DESCRIPTION,
        supported_interfaces=[AgentInterface(
            url=f"http://localhost:{AGENT_SERVER_PORT}",
            protocol_binding="JSONRPC",
        )],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(
            id="hotel-booking",
            name="hotel-booking",
            description=(
                "Search hotels by city, price, rating, and amenities. "
                "Create, modify, and cancel reservations. "
                "View booking details and hotel reviews."
            ),
            tags=["hotel", "booking", "reservation", "travel"],
        )],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )


# --- Application ---

def create_app():
    agent = MCPAgent()
    handler = DefaultRequestHandler(
        agent_executor=HotelAgentExecutor(agent),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=create_agent_card(), http_handler=handler,
        context_builder=DefaultCallContextBuilder(),
        enable_v0_3_compat=True,
    )
    return a2a_app.build(), agent


def main():
    logging.getLogger().setLevel(logging.INFO)
    app, agent = create_app()

    @asynccontextmanager
    async def lifespan(app):
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
