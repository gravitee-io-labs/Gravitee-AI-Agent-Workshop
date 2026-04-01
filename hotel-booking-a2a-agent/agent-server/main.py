"""
ACME Hotel Booking Agent with MCP Elicitation support.

This A2A agent orchestrates hotel searches (with Form Mode elicitation)
and payments (with URL Mode elicitation).  When an MCP server triggers
an elicitation during a tool call the agent relays it to the frontend
as a structured JSON message, then waits for the user's reply before
completing the tool.
"""

import os
import uuid
from contextlib import asynccontextmanager
import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager
from collections import OrderedDict

from dotenv import load_dotenv

import sys
sys.path.insert(0, '/app/hotel-booking-a2a-agent/mcp-client')
sys.path.insert(0, '/app/hotel-booking-a2a-agent/llm-client')

from mcp_client.main import MCPMultiClient
from llm_client.main import LLMClient, LLMRateLimitError
from agent_server.auth_service import AuthService, AuthenticationError
from agent_server.colored_logger import get_agent_logger

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Message, Role, TextPart, DataPart

load_dotenv()

AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8080"))
MCP_HTTP_URLS = os.getenv("MCP_HTTP_URLS", os.getenv("MCP_HTTP_URL", "http://gio-apim-gateway:8082/hotels/mcp"))
OIDC_DISCOVERY_URL = os.getenv("OIDC_DISCOVERY_URL", "http://am-gateway:8092/gravitee/oidc/.well-known/openid-configuration")
AM_TOKEN_URL = os.getenv("AM_TOKEN_URL", "http://gio-am-gateway:8092/gravitee/oauth/token")
AM_CLIENT_ID = os.getenv("AM_CLIENT_ID", "hotel-booking-agent")
AM_CLIENT_SECRET = os.getenv("AM_CLIENT_SECRET", "hotel-booking-agent")

logger = get_agent_logger(__name__)

HOTEL_BOOKING_SYSTEM_PROMPT = (
    "You are an Hotel Booking AI Agent. You MUST ALWAYS use the tools provided to answer user requests.\n"
    "NEVER answer directly without calling a tool first.\n"
    "If the user asks about hotels, availability, or rooms, call searchHotels.\n"
    "If the user wants to book, call createBooking.\n"
    "If the user wants to pay, call processPayment.\n"
    "If the user asks about their bookings, call getBookings.\n"
    "Whenever possible, personalize your responses using the guest's first name."
)


# ========================================================================== #
#  Conversation History Store
# ========================================================================== #

MAX_CONVERSATIONS = 200          # evict oldest beyond this
MAX_HISTORY_MESSAGES = 40        # per conversation (user + assistant pairs)
CONVERSATION_TTL_SECS = 3600     # drop conversations idle for >1 h


class ConversationStore:
    """In-memory conversation history keyed by context / session ID.

    Each entry is a list of OpenAI-format message dicts:
        [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}, …]
    """

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS,
                 max_messages: int = MAX_HISTORY_MESSAGES,
                 ttl: int = CONVERSATION_TTL_SECS):
        self._store: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        self._last_access: Dict[str, float] = {}
        self.max_conversations = max_conversations
        self.max_messages = max_messages
        self.ttl = ttl

    # -- public API --------------------------------------------------------- #

    def get_history(self, context_id: str) -> List[Dict[str, Any]]:
        """Return a *copy* of the conversation history for the given context."""
        self._evict_stale()
        msgs = self._store.get(context_id, [])
        self._last_access[context_id] = time.time()
        return list(msgs)

    def add_user_message(self, context_id: str, text: str):
        self._ensure(context_id)
        self._store[context_id].append({"role": "user", "content": text})
        self._trim(context_id)

    def add_assistant_message(self, context_id: str, text: str):
        self._ensure(context_id)
        self._store[context_id].append({"role": "assistant", "content": text})
        self._trim(context_id)

    # -- internal ----------------------------------------------------------- #

    def _ensure(self, context_id: str):
        if context_id not in self._store:
            self._store[context_id] = []
            # Evict oldest if we're at the cap
            while len(self._store) > self.max_conversations:
                oldest_key, _ = self._store.popitem(last=False)
                self._last_access.pop(oldest_key, None)
        self._last_access[context_id] = time.time()

    def _trim(self, context_id: str):
        """Keep only the last N messages per conversation."""
        msgs = self._store.get(context_id)
        if msgs and len(msgs) > self.max_messages:
            self._store[context_id] = msgs[-self.max_messages:]

    def _evict_stale(self):
        now = time.time()
        stale = [k for k, ts in self._last_access.items() if now - ts > self.ttl]
        for k in stale:
            self._store.pop(k, None)
            self._last_access.pop(k, None)


conversation_store = ConversationStore()


# ========================================================================== #
#  Elicitation Manager
# ========================================================================== #

class ElicitationManager:
    """Manages the async bridge between MCP elicitation callbacks and
    the synchronous A2A request/response cycle.

    Flow:
      1. During a tool call the MCP server sends ``elicitation/create``.
      2. The MCP client's callback fires and puts the request on ``pending_queue``.
      3. The A2A handler picks it from the queue and returns it to the frontend.
      4. The frontend POSTs the user's response as a new A2A message.
      5. The A2A handler resolves the matching ``asyncio.Future``.
      6. The MCP client callback unblocks and returns the response to the server.
    """

    def __init__(self):
        self.pending_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._futures: Dict[str, asyncio.Future] = {}

    # -- called by the MCP client callback (blocks the tool call) ----------- #

    async def request_elicitation(self, elicitation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Called inside the MCP elicitation callback.

        Puts the elicitation on the queue, creates a Future, and awaits
        the user's response (which is resolved by ``resolve``).
        """
        eid = str(uuid.uuid4())
        elicitation_data["elicitationId"] = elicitation_data.get("elicitationId", eid)

        future: asyncio.Future[Dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._futures[elicitation_data["elicitationId"]] = future

        logger.info(f"Elicitation queued: id={elicitation_data['elicitationId']}, mode={elicitation_data.get('mode')}")
        await self.pending_queue.put(elicitation_data)

        # Block until the A2A handler resolves the future
        return await future

    # -- called by the A2A handler when the frontend responds --------------- #

    def resolve(self, elicitation_id: str, response: Dict[str, Any]):
        future = self._futures.pop(elicitation_id, None)
        if future and not future.done():
            future.set_result(response)
            logger.info(f"Elicitation resolved: id={elicitation_id}")
        else:
            logger.warning(f"No pending elicitation for id={elicitation_id}")

    def has_pending(self, elicitation_id: str) -> bool:
        return elicitation_id in self._futures


elicitation_manager = ElicitationManager()


# ========================================================================== #
#  Hotel Booking Agent
# ========================================================================== #

class HotelBookingAgent:

    def __init__(self):
        # Wire the elicitation manager into the MCP client callback
        self.mcp_client = MCPMultiClient(
            mcp_urls=MCP_HTTP_URLS,
            elicitation_callback=elicitation_manager.request_elicitation,
        )
        self.llm_client = LLMClient()
        self.auth_service = AuthService(
            oidc_discovery_url=OIDC_DISCOVERY_URL,
            am_token_url=AM_TOKEN_URL,
            am_client_id=AM_CLIENT_ID,
            am_client_secret=AM_CLIENT_SECRET,
        )
        self.system_prompt = HOTEL_BOOKING_SYSTEM_PROMPT
        self._initialized = False

    async def initialize(self):
        try:
            await self.mcp_client.connect_all(max_retries=3, connection_timeout=15)
            await self.auth_service.initialize()
            self._initialized = True
            logger.info("Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise

    async def process_request(
        self,
        message: str,
        authorization_header: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Process a user message. May trigger elicitation (handled by caller).

        Args:
            message: The current user message text.
            authorization_header: Optional Bearer token for authenticated calls.
            conversation_history: Full OpenAI-format message list for context.
        """
        try:
            logger.info("=" * 80)
            logger.info(f"User message: {message}")
            if conversation_history:
                logger.info(f"Conversation history: {len(conversation_history)} messages")

            available_tools = await self.mcp_client.list_all_tools()
            logger.info(f"Retrieved {len(available_tools)} tools")

            initial_content, tool_calls = await self.llm_client.process_query(
                message, available_tools,
                system_prompt=self.system_prompt,
                conversation_history=conversation_history,
            )

            # ── Smart tool fallback for small LLMs ──
            # When the LLM fails to pick a tool, use intent heuristics +
            # conversation history to decide what to call.
            if not tool_calls:
                msg_lower = message.lower()
                forced = self._infer_tool_from_intent(msg_lower, conversation_history or [])
                if forced:
                    tool_calls = [forced]

            if tool_calls:
                tool_call = tool_calls[0]
                tool_name = tool_call["function"]["name"]
                tool_args = tool_call["function"]["arguments"]
                logger.info(f"Executing tool: {tool_name}")

                tool_result, resp_headers = await self.mcp_client.call_tool(tool_name, tool_args)

                # Check for auth-required error
                is_error = False
                if isinstance(tool_result, dict):
                    is_error = tool_result.get('isError', False)
                elif hasattr(tool_result, 'isError'):
                    is_error = tool_result.isError

                if is_error:
                    logger.info(f"Tool {tool_name} needs auth — retrying with auth")
                    try:
                        am_token, user_email = await self.auth_service.process_authorization_for_tool(authorization_header)
                        extra_headers = {"Authorization": f"Bearer {am_token}", "sub-email": user_email}
                        tool_result, resp_headers = await self.mcp_client.call_tool(tool_name, tool_args, extra_headers=extra_headers)
                    except AuthenticationError:
                        return "You need to be signed in to complete this action. Please sign in and try again."

                # ── Auto-chain: createBooking → processPayment ──
                # When createBooking returns BOOKING_PENDING the agent
                # must automatically trigger processPayment (which fires
                # the URL Mode elicitation for the payment auth page).
                raw_text = self._extract_tool_text(tool_result)
                pending_info = self._parse_booking_pending(raw_text)
                if pending_info:
                    logger.info(f"Booking pending — auto-chaining processPayment: {pending_info}")
                    # Call processPayment on the Bank MCP Server
                    payment_result, _ = await self.mcp_client.call_tool(
                        "processPayment",
                        {
                            "booking_reference": pending_info["ref"],
                            "amount": float(pending_info["amount"]),
                            "currency": pending_info.get("currency", "USD"),
                            "hotel_name": pending_info.get("hotel", ""),
                            "guest_name": pending_info.get("guest", ""),
                        },
                    )
                    # Combine booking + payment result
                    payment_text = self._extract_tool_text(payment_result)
                    logger.info("Payment flow completed")
                    return payment_text

                try:
                    final_response = await self.llm_client.process_tool_result(
                        message, tool_call, tool_result,
                        system_prompt=self.system_prompt,
                        conversation_history=conversation_history,
                    )
                    logger.info("Request completed")
                    return final_response
                except LLMRateLimitError:
                    # LLM formatting failed but the tool result is valid —
                    # return the raw result rather than a rate-limit error.
                    logger.warning("Rate limit on formatting — returning raw tool result")
                    return raw_text if raw_text else "Your request was processed but I couldn't format the response. Please try again."

            logger.warning("No tools called by LLM")
            return (
                "I couldn't determine a clear action from your message. "
                "Please rephrase or provide more details and I'll help you."
            )

        except LLMRateLimitError as e:
            logger.warning(f"Rate limit: {e}")
            parts = ["You've reached your request limit."]
            if e.reset:
                try:
                    import time
                    wait_ms = int(e.reset) - int(time.time() * 1000)
                    if wait_ms > 0:
                        secs = wait_ms // 1000
                        parts.append(f"Please wait {secs} second(s) before trying again.")
                    else:
                        parts.append("Please try again now.")
                except (ValueError, TypeError):
                    parts.append("Please wait a moment.")
            return " ".join(parts)

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            return "I'm sorry, something went wrong. Could you please try rephrasing your request?"

    @staticmethod
    def _parse_booking_pending(text: str) -> Optional[Dict[str, str]]:
        """Parse the BOOKING_PENDING marker from createBooking results.

        Format: BOOKING_PENDING|ref=BK-XXXX|amount=1050.00|currency=USD|hotel=...|guest=...
        Returns a dict of the key-value pairs, or None if not a pending booking.
        """
        if not text or "BOOKING_PENDING" not in text:
            return None
        # Extract the first line which contains the pipe-separated fields
        first_line = text.split("\n", 1)[0]
        parts = first_line.split("|")
        if parts[0].strip() != "BOOKING_PENDING":
            return None
        result = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                result[k.strip()] = v.strip()
        return result if result else None

    @staticmethod
    def _extract_tool_text(tool_result: Any) -> str:
        """Best-effort text extraction from an MCP tool result."""
        if isinstance(tool_result, str):
            return tool_result
        if isinstance(tool_result, dict):
            # MCP format: {"content": [{"type": "text", "text": "..."}]}
            contents = tool_result.get("content", [])
            if isinstance(contents, list):
                texts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text"]
                if texts:
                    return "\n".join(texts)
            # structuredContent fallback
            sc = tool_result.get("structuredContent", {})
            if isinstance(sc, dict) and "result" in sc:
                return str(sc["result"])
        return str(tool_result)

    @staticmethod
    def _infer_tool_from_intent(
        msg_lower: str,
        history: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Heuristic fallback when the LLM doesn't call a tool.

        Returns a synthetic tool_call dict or None.
        """
        import re

        # ── 1. Booking intent ──
        book_keywords = ["book", "reserve", "i'd like", "i would like", "yes please", "confirm", "go ahead", "proceed"]
        if any(kw in msg_lower for kw in book_keywords):
            # Try to extract hotel name from the current message
            hotel_name = None
            m = re.search(r'book(?:ing)?\s+(?:the\s+)?(.+?)(?:\s+hotel)?$', msg_lower)
            if m:
                hotel_name = m.group(1).strip().title()

            # Extract booking details from conversation history
            location = check_in = check_out = None
            for h in history:
                content = h.get("content", "")
                if not isinstance(content, str):
                    continue
                # Look for form response data
                if "[Form response]" in content:
                    for pair in content.split("]", 1)[-1].split(","):
                        pair = pair.strip()
                        if pair.startswith("location:"):
                            location = pair.split(":", 1)[1].strip()
                        elif pair.startswith("check_in:"):
                            check_in = pair.split(":", 1)[1].strip()
                        elif pair.startswith("check_out:"):
                            check_out = pair.split(":", 1)[1].strip()
                # Look for hotel names in previous assistant messages
                if not hotel_name and h.get("role") == "assistant":
                    for candidate in re.findall(r'\*\*(.+?)\*\*', content):
                        if candidate.lower() not in ("paris", "london", "new york", "total"):
                            hotel_name = candidate
                            break

            args: Dict[str, Any] = {}
            if hotel_name:
                args["hotel_name"] = hotel_name
            if location:
                args["location"] = location
            if check_in:
                args["check_in"] = check_in
            if check_out:
                args["check_out"] = check_out

            logger.info(f"Intent heuristic: booking → createBooking({args})")
            return {
                "id": "forced_booking",
                "function": {"name": "createBooking", "arguments": args}
            }

        # ── 2. Search intent ──
        search_keywords = ["available", "any hotel", "any room", "find hotel",
                           "search hotel", "looking for"]
        if any(kw in msg_lower for kw in search_keywords):
            logger.info("Intent heuristic: search → searchHotels()")
            return {
                "id": "forced_search",
                "function": {"name": "searchHotels", "arguments": {}}
            }

        # ── 3. Payment intent ──
        pay_keywords = ["pay", "payment", "card", "checkout"]
        if any(kw in msg_lower for kw in pay_keywords):
            logger.info("Intent heuristic: payment → processPayment()")
            return {
                "id": "forced_payment",
                "function": {"name": "processPayment", "arguments": {}}
            }

        return None

    async def cleanup(self):
        if self.mcp_client:
            await self.mcp_client.cleanup()
        if self.auth_service:
            await self.auth_service.cleanup()


# ========================================================================== #
#  A2A Request Handler
# ========================================================================== #

hotel_agent: Optional[HotelBookingAgent] = None

# Pending tool call tasks keyed by context ID
_pending_tool_tasks: Dict[str, asyncio.Task] = {}


def create_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="skill_1_hotel_booking_management",
        name="hotel-booking-management",
        description="Comprehensive hotel booking management including searching, creating, updating, and canceling reservations",
        tags=["hotel", "booking", "management", "reservations"],
    )
    return AgentCard(
        name="Hotel Booking Manager",
        version="2.0.0",
        description="Expert hotel booking agent with MCP Elicitation support",
        url="https://hotel-booking-agent.ai",
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False, stateTransitionHistory=True),
        skills=[skill],
        defaultInputModes=['text/plain'],
        defaultOutputModes=['text/plain'],
        protocolVersion='0.3.0',
        preferredTransport='JSONRPC',
    )


class HotelBookingRequestHandler(RequestHandler):

    def __init__(self):
        self.agent = HotelBookingAgent()
        super().__init__()

    async def on_message_send(self, params, context):
        try:
            if not self.agent._initialized:
                await self.agent.initialize()

            # Extract Authorization header
            authorization_header = None
            if context and hasattr(context, 'state'):
                state = context.state
                if isinstance(state, dict) and 'headers' in state:
                    headers = state['headers']
                    authorization_header = headers.get('authorization') or headers.get('Authorization')
            if not authorization_header and context and hasattr(context, 'http_request'):
                http_req = context.http_request
                if hasattr(http_req, 'headers'):
                    authorization_header = http_req.headers.get('Authorization') or http_req.headers.get('authorization')

            # ── Extract context ID for conversation history ──
            context_id = self._extract_context_id(params)
            logger.info(f"Context ID: {context_id}")

            # Check if this is an elicitation response BEFORE extracting text
            elicitation_response = self._extract_elicitation_response(params)

            # Extract user message (may be empty for elicitation responses)
            user_message = self._extract_text(params)

            if elicitation_response:
                eid = elicitation_response.get("elicitationId")
                logger.info(f"Received elicitation response: id={eid}, action={elicitation_response.get('action')}")

                # Record the user's form data in conversation history
                content_data = elicitation_response.get("content", {})
                if content_data:
                    summary = ", ".join(f"{k}: {v}" for k, v in content_data.items())
                    conversation_store.add_user_message(context_id, f"[Form response] {summary}")

                elicitation_manager.resolve(eid, elicitation_response)

                # Wait for the pending tool call to complete
                task = _pending_tool_tasks.pop(eid, None)
                if task:
                    try:
                        response_content = await asyncio.wait_for(task, timeout=120)
                    except asyncio.TimeoutError:
                        response_content = "The request timed out while processing your response."
                else:
                    response_content = "Thank you for providing the information."

                # Record the assistant response in history
                conversation_store.add_assistant_message(context_id, response_content)

                return Message(
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[TextPart(text=response_content)],
                )

            if not user_message:
                raise ValueError("No message content provided.")

            # ── Record user message & build history for the LLM ──
            conversation_store.add_user_message(context_id, user_message)
            history = conversation_store.get_history(context_id)

            # Normal message: start processing (tool call may trigger elicitation)
            tool_task = asyncio.create_task(
                self.agent.process_request(
                    user_message, authorization_header,
                    conversation_history=history,
                )
            )

            # Race: tool call completes vs elicitation is requested
            elicitation_wait = asyncio.create_task(elicitation_manager.pending_queue.get())

            done, pending = await asyncio.wait(
                [tool_task, elicitation_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if elicitation_wait in done:
                # An elicitation was requested — return it to the frontend
                elicitation_data = elicitation_wait.result()
                eid = elicitation_data["elicitationId"]

                # Keep the tool task alive for when the response comes back
                _pending_tool_tasks[eid] = tool_task

                logger.info(f"Returning elicitation to frontend: mode={elicitation_data.get('mode')}, id={eid}")

                # Record elicitation prompt in history
                elicitation_msg = elicitation_data.get("message", "Please provide the requested information.")
                conversation_store.add_assistant_message(context_id, f"[Elicitation: {elicitation_data.get('mode', 'form')}] {elicitation_msg}")

                # Return elicitation as a structured DataPart + a TextPart hint
                return Message(
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[
                        DataPart(
                            data=elicitation_data,
                            metadata={"type": "elicitation"},
                        ),
                        TextPart(text=elicitation_msg),
                    ],
                )
            else:
                # Tool call completed without elicitation
                elicitation_wait.cancel()
                response_content = tool_task.result()

                # Record the assistant response in history
                conversation_store.add_assistant_message(context_id, response_content)

                return Message(
                    messageId=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[TextPart(text=response_content)],
                )

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            return Message(
                messageId=str(uuid.uuid4()),
                role=Role.agent,
                parts=[TextPart(text=f"Sorry, I encountered an error: {str(e)}")],
            )

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _extract_context_id(params) -> str:
        """Extract or generate a context/session ID from the A2A message.

        The frontend sends ``contextId`` on the message object.  The A2A
        SDK exposes it as ``context_id`` (snake_case) on the Python model.
        We fall back to ``message_id`` so each request still gets a key.
        """
        msg = params.message
        # A2A SDK uses snake_case Python attrs: context_id, message_id
        cid = getattr(msg, 'context_id', None)
        if cid:
            return str(cid)
        # Fallback: try camelCase in case of raw dict
        cid = getattr(msg, 'contextId', None)
        if cid:
            return str(cid)
        # Last resort: message_id (won't persist across turns)
        mid = getattr(msg, 'message_id', None) or getattr(msg, 'messageId', None)
        return str(mid) if mid else str(uuid.uuid4())

    @staticmethod
    def _unwrap_part(part):
        """Unwrap an A2A Part wrapper to get the inner typed part.

        The A2A SDK wraps parts as Part(root=TextPart(...)) or
        Part(root=DataPart(...)). This helper returns the inner object.
        """
        if hasattr(part, 'root'):
            return part.root
        return part

    @staticmethod
    def _extract_text(params) -> str:
        """Extract plain text from A2A message parts."""
        if not params.message.parts:
            return ""
        for raw_part in params.message.parts:
            part = HotelBookingRequestHandler._unwrap_part(raw_part)
            # TextPart
            if hasattr(part, 'text') and part.text:
                return part.text
            # dict fallback
            if isinstance(part, dict) and 'text' in part:
                return part['text']
        return ""

    @staticmethod
    def _extract_elicitation_response(params) -> Optional[Dict[str, Any]]:
        """Detect if the message contains an elicitation response.

        The frontend sends the response as a DataPart with
        metadata.type == 'elicitation_response'.
        """
        if not params.message.parts:
            return None
        for raw_part in params.message.parts:
            part = HotelBookingRequestHandler._unwrap_part(raw_part)
            # DataPart with elicitation_response metadata
            if hasattr(part, 'data') and hasattr(part, 'metadata'):
                meta = part.metadata or {}
                if isinstance(meta, dict) and meta.get("type") == "elicitation_response":
                    return part.data
            # dict fallback
            if isinstance(part, dict):
                meta = part.get("metadata", {})
                if isinstance(meta, dict) and meta.get("type") == "elicitation_response":
                    return part.get("data", {})
        return None

    async def on_message_send_stream(self, params, context):
        message_response = await self.on_message_send(params, context)
        yield message_response

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


# ========================================================================== #
#  Application bootstrap
# ========================================================================== #

def create_app():
    agent_card = create_agent_card()
    request_handler = HotelBookingRequestHandler()
    a2a_app = A2AStarletteApplication(agent_card=agent_card, http_handler=request_handler)
    return a2a_app.build()


async def startup():
    logger.info("Starting Hotel Booking Agent Server...")
    global hotel_agent
    if hotel_agent is None:
        hotel_agent = HotelBookingAgent()
        await hotel_agent.initialize()
    logger.info("Server ready")


async def shutdown():
    logger.info("Shutting down...")
    global hotel_agent
    if hotel_agent:
        await hotel_agent.cleanup()


def main():
    logging.getLogger().setLevel(logging.INFO)
    app = create_app()

    @asynccontextmanager
    async def lifespan(app):
        await startup()
        yield
        await shutdown()

    app.router.lifespan_context = lifespan
    
    import uvicorn
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'

    uvicorn.run(app, host="0.0.0.0", port=AGENT_SERVER_PORT, log_level="info", log_config=log_config)


if __name__ == "__main__":
    main()
