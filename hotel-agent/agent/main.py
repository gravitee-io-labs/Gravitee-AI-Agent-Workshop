"""ACME Hotel Agent — A2A 1.0 + MCP + RFC 8693 Token Exchange."""

import os
import re
import uuid
import asyncio
import json
import logging
import time
import unicodedata
from typing import Any
from contextlib import asynccontextmanager
from collections import OrderedDict

from dotenv import load_dotenv
from google.protobuf import struct_pb2

from agent.mcp_client import MCPMultiClient, ToolError
from agent.llm_client import LLMClient, LLMRateLimitError, LLMRequestBlockedError
import math
from agent.auth_service import AuthService, AuthenticationError
from agent.logger import get_agent_logger

from starlette.applications import Starlette
from starlette.routing import Route

from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.request_handlers.default_request_handler import LegacyRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, AgentInterface, Part
from a2a.helpers.proto_helpers import new_text_message, new_message

load_dotenv()

# --- Configuration ---

AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
AGENT_PUBLIC_URL = os.getenv("AGENT_PUBLIC_URL", f"http://localhost:{AGENT_SERVER_PORT}")
AGENT_NAME = os.getenv("AGENT_NAME", "ACME Hotel Agent")
AGENT_DESCRIPTION = os.getenv(
    "AGENT_DESCRIPTION",
    "AI-powered hotel booking assistant. Searches hotels, manages reservations, "
    "and helps guests with all aspects of their stay.",
)
MCP_HTTP_URLS = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", ""))
MCP_API_KEY = os.getenv("MCP_API_KEY", "")
AM_TOKEN_URL = os.getenv("AM_TOKEN_URL", "")
AM_CLIENT_ID = os.getenv("AM_CLIENT_ID", "")
AM_CLIENT_SECRET = os.getenv("AM_CLIENT_SECRET", "")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a Hotel booking assistant. "
    "Help guests search for hotels, check availability, make reservations, and manage their bookings. "
    "Always use the available tools to retrieve data, never invent or fabricate information. "
    "To search for hotels or check availability, call searchHotels immediately — even if the user has "
    "not given a city or dates. Never ask the user for the city or dates yourself; searchHotels collects "
    "any missing details directly from the user. "
    "When the user chooses a specific hotel to book, call createBooking — use that hotel's id from the "
    "earlier search results and reuse the same city, dates and guest count already provided earlier in "
    "this conversation. Do NOT call searchHotels again once the user has picked a hotel. "
    "When asked for information beyond hotel bookings (local attractions, restaurants, things to do, current events, etc.), "
    "use the web search tool if available to answer the question. "
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

    def reset(self):
        """Abandon any in-flight/queued elicitation. Needed because a single MCP
        session can't run two tool calls at once: if a paused elicitation's tool
        call is left running, a new request's tool call deadlocks behind it. The
        orphaned form is recovered in _handle_elicitation (re-runs the search)."""
        while not self.pending_queue.empty():
            try:
                self.pending_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()
        self._futures.clear()


elicitation_mgr = ElicitationManager()


# --- MCP Agent (4-steps pipeline) ---

def _rate_limit_message(e: LLMRateLimitError) -> str:
    """Build a user-friendly rate limit message from LLM headers."""
    if e.reset:
        try:
            reset_epoch_s = int(e.reset) / 1000
            wait = max(1, math.ceil(reset_epoch_s - time.time()))
            return f"You're sending too many requests and have been rate limited. Please try again in {wait} seconds."
        except (ValueError, TypeError):
            pass
    return "You're sending too many requests and have been rate limited. Please try again in a few seconds."


def _norm(s: str) -> str:
    """Lowercase + strip accents for tolerant hotel-name matching."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


_BOOKING_INTENT = re.compile(
    r"\b(book|reserve|reservation|let'?s go|i'?ll take|i will take|go (for|with|ahead)|"
    r"choose|select|pick|confirm)\b",
    re.IGNORECASE,
)


def _extract_search_payload(content: str) -> dict[str, Any] | None:
    """Pull the {hotels, check_in, ...} payload out of a stored tool result,
    handling both the flat dict and the nested MCP result wrapper
    ({"content":[{"text": "<json>"}], "structuredContent": {...}})."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("hotels"), list):
        return data
    sc = data.get("structuredContent")
    if isinstance(sc, dict) and isinstance(sc.get("hotels"), list):
        return sc
    for c in (data.get("content") or []):
        text = c.get("text") if isinstance(c, dict) else None
        if text and "hotels" in text:
            try:
                inner = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(inner, dict) and isinstance(inner.get("hotels"), list):
                return inner
    return None


def _last_search_context(history: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Find the most recent searchHotels result in the conversation history and
    return its hotels list plus the dates/guests that were used."""
    if not history:
        return None
    for msg in reversed(history):
        content = msg.get("content")
        if not content or not isinstance(content, str) or "hotels" not in content:
            continue
        payload = _extract_search_payload(content)
        if payload:
            return payload
    return None


def _maybe_booking_shortcut(
    message: str,
    history: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Small models can't reliably switch from searching to booking. When the
    user expresses a booking intent AND names a hotel from the previous search
    results, force a createBooking call reusing the remembered dates/guests."""
    if not any(t["function"]["name"] == "createBooking" for t in tools):
        return None
    if not _BOOKING_INTENT.search(message or ""):
        return None
    ctx = _last_search_context(history)
    if not ctx:
        return None

    msg_norm = _norm(message)
    chosen = None
    for hotel in ctx["hotels"]:
        name = hotel.get("name", "")
        hid = hotel.get("id", "")
        if name and _norm(name) in msg_norm:
            chosen = hotel
            break
        if hid and _norm(hid.replace("-", " ")) in msg_norm:
            chosen = hotel
            break
    if not chosen:
        return None

    args = {
        "hotel_id": chosen.get("id"),
        "check_in": ctx.get("check_in"),
        "check_out": ctx.get("check_out"),
        "guests": ctx.get("guests", 2),
    }
    return {"id": "forced_booking", "function": {"name": "createBooking", "arguments": args}}


class MCPAgent:

    def __init__(self):
        static_headers = {"Authorization": f"Bearer {MCP_API_KEY}"} if MCP_API_KEY else None
        self.mcp = MCPMultiClient(mcp_urls=MCP_HTTP_URLS, elicitation_callback=elicitation_mgr.request, static_headers=static_headers)
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
        self, message: str, token: str | None = None,
        transaction_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        # Build gateway headers: transaction ID for correlation + auth for MCP
        gw_headers = {}
        if transaction_id:
            gw_headers["X-Gravitee-Transaction-Id"] = transaction_id
        mcp_headers = dict(gw_headers)
        if MCP_API_KEY:
            mcp_headers["Authorization"] = f"Bearer {MCP_API_KEY}"
        elif token:
            mcp_headers["Authorization"] = f"Bearer {token}"

        # Step 1 — MCP Tools Discovery
        tools = await self.mcp.list_all_tools(extra_headers=mcp_headers or None)
        logger.info(f"Step 1 - MCP Tools Discovery: {len(tools)} tools availables.")

        # Build a tool-aware system prompt so small models know the exact tool names
        effective_system_prompt = SYSTEM_PROMPT
        if tools:
            tool_names = ", ".join(t["function"]["name"] for t in tools)
            effective_system_prompt += (
                f"\n\nAvailable tools: {tool_names}. "
                "You MUST call one of these tools for every user request. "
                "Never answer from your own knowledge or say you cannot help — always use a tool."
            )

        # Step 2 — LLM decides which tool to call. Pass prior turns so it can reuse
        # details already gathered (city/dates) and book the chosen hotel instead
        # of re-searching.
        # Shortcut: if the message contains "web", force tavily_search if available
        web_tool = next((t for t in tools if t["function"]["name"] == "tavily_search"), None)
        booking_call = _maybe_booking_shortcut(message, conversation_history, tools)
        if booking_call:
            logger.info(f"Step 2 - Shortcut: booking intent detected, forcing createBooking({json.dumps(booking_call['function']['arguments'])[:120]})")
            tool_calls = [booking_call]
        elif web_tool and "web" in message.lower():
            logger.info("Step 2 - Shortcut: 'web' keyword detected, forcing tavily_search")
            tool_calls = [{"id": "forced_web_search", "function": {"name": "tavily_search", "arguments": {"query": message}}}]
        else:
            try:
                content, tool_calls = await self.llm.process_query(
                    message, tools, system_prompt=effective_system_prompt,
                    conversation_history=conversation_history,
                    extra_headers=gw_headers or None,
                )
            except LLMRateLimitError as e:
                logger.warning(f"Step 2 - Rate limited (reset={e.reset})")
                return _rate_limit_message(e), []
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

        try:
            result, _ = await self.mcp.call_tool(tool_name, tool_args, extra_headers=mcp_headers or None)
        except ToolError as e:
            logger.error(f"Step 3 - Tool Execution: {tool_name} failed ({e.status_code}): {e}")
            if e.status_code in (401, 403):
                return "You need to sign in to perform this action. Please log in and try again.", []
            return f"Sorry, something went wrong: {e}", []
        logger.info(f"Step 3 - Tool Execution: {tool_name} succeeded.")

        tool_messages = self._build_tool_messages(tc, tool_name, tool_args, result)

        # Step 4 — Reflect, LLM formats result for user, given the tool response
        try:
            response = await self.llm.process_tool_result(
                message, tc, result, system_prompt=SYSTEM_PROMPT,
                extra_headers=gw_headers or None,
            )
            logger.info(f"Step 4 - LLM formatting: successfully formatted the tool result for user response.")
            return response, tool_messages
        except LLMRateLimitError as e:
            logger.warning(f"Step 4 - Rate limited (reset={e.reset})")
            return _rate_limit_message(e), tool_messages
        except LLMRequestBlockedError:
            logger.warning("Step 4 - LLM call failed because the response was blocked by safety filters.")
            return "Your request was blocked because it was deemed invalid or unsafe.", tool_messages
        except Exception as e:
            logger.error(f"Step 4 — LLM call failed ({type(e).__name__}: {e}), returning raw result")
            return json.dumps(result) if isinstance(result, (dict, list)) else str(result), tool_messages

    async def cleanup(self):
        await self.mcp.cleanup()
        await self.auth.cleanup()

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
            transaction_id = self._get_transaction_id(context)
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

            # A fresh typed message abandons any prior elicitation. Cancel its
            # tool task — a single MCP session can't run two tool calls at once,
            # so leaving a paused one alive deadlocks this turn. An answer to the
            # abandoned form is still recovered in _handle_elicitation.
            elicitation_mgr.reset()
            for stale in _pending_tasks.values():
                stale.cancel()
            _pending_tasks.clear()

            logger.info(f"User prompt: {user_text[:150]}")
            conversations.add(context_id, "user", user_text)

            # Race: pipeline vs elicitation request. Pass conversation history (which
            # already includes this turn) so the model can reuse earlier details.
            history = conversations.get(context_id)
            tool_task = asyncio.create_task(self.agent.process(
                user_text, token, transaction_id=transaction_id, conversation_history=history))
            elicitation_wait = asyncio.create_task(elicitation_mgr.pending_queue.get())
            done, _ = await asyncio.wait({tool_task, elicitation_wait}, return_when=asyncio.FIRST_COMPLETED)

            if elicitation_wait in done:
                elicitation_data = elicitation_wait.result()
                eid = elicitation_data["elicitationId"]
                _pending_tasks[eid] = tool_task
                msg = elicitation_data.get("message", "Please provide the requested information.")
                conversations.add(context_id, "assistant", f"[Elicitation] {msg}")
                await event_queue.enqueue_event(new_message(
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
        await eq.enqueue_event(new_text_message(text, context_id=context_id, task_id=task_id))

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
    def _get_transaction_id(context: RequestContext) -> str | None:
        """Extract the Gravitee Transaction ID from the incoming request.

        When provided on outgoing calls, the gateway preserves it instead of
        generating a new one — linking all sub-calls into a single transaction.
        """
        if context.call_context:
            headers = context.call_context.state.get("headers", {})
            return (headers.get("X-Gravitee-Transaction-Id")
                    or headers.get("x-gravitee-transaction-id"))
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
            url=AGENT_PUBLIC_URL,
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
    agent_card = create_agent_card()
    task_store = InMemoryTaskStore()
    context_builder = DefaultServerCallContextBuilder()

    handler = LegacyRequestHandler(
        agent_executor=HotelAgentExecutor(agent),
        task_store=task_store,
        agent_card=agent_card,
    )

    routes = (
        create_agent_card_routes(agent_card)
        + create_jsonrpc_routes(
            handler,
            rpc_url="/",
            context_builder=context_builder,
            enable_v0_3_compat=True,
        )
    )
    app = Starlette(routes=routes)
    return app, agent


def main():
    logging.getLogger().setLevel(logging.INFO)
    app, agent = create_app()

    @asynccontextmanager
    async def lifespan(app):
        try:
            await agent.initialize()
            logger.info("Agent ready")
        except Exception as e:
            logger.warning(f"Agent initialization failed, will retry on first request: {e}")
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
