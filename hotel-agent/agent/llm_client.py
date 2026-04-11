"""LLM Client using OpenAI-compatible API."""
import json
import logging
import os
from typing import Any

from openai import OpenAI, RateLimitError, BadRequestError
from dotenv import load_dotenv

from agent.logger import get_llm_logger

load_dotenv()
logger = get_llm_logger(__name__)


class LLMRateLimitError(Exception):
    def __init__(self, message: str, limit: str | None = None,
                 remaining: str | None = None, reset: str | None = None):
        super().__init__(message)
        self.limit = limit
        self.remaining = remaining
        self.reset = reset


class LLMRequestBlockedError(Exception):
    pass


LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://gio-apim-gateway:8082/llm/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:0.6b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))


class LLMClient:
    """OpenAI-compatible LLM client with tool calling support."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, temperature: float | None = None):
        self.client = OpenAI(
            base_url=base_url or LLM_BASE_URL,
            api_key=api_key or LLM_API_KEY,
        )
        self.model = model or LLM_MODEL
        self.temperature = temperature or LLM_TEMPERATURE

    def _handle_rate_limit(self, e: RateLimitError):
        headers = getattr(e.response, 'headers', {}) if hasattr(e, 'response') else {}
        raise LLMRateLimitError(
            "Rate limit exceeded",
            limit=headers.get('X-Token-Rate-Limit-Limit'),
            remaining=headers.get('X-Token-Rate-Limit-Remaining'),
            reset=headers.get('X-Token-Rate-Limit-Reset'),
        ) from e

    async def process_query(
        self,
        query: str,
        available_tools: list[dict[str, Any]],
        system_prompt: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Send query + tools to LLM, return (content, tool_calls)."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if conversation_history:
            messages.extend(conversation_history)
        else:
            messages.append({"role": "user", "content": query})

        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if available_tools:
            params["tools"] = available_tools
            params["tool_choice"] = "required"
        if extra_headers:
            params["extra_headers"] = extra_headers

        try:
            response = self.client.chat.completions.create(**params)
        except BadRequestError as e:
            logger.warning(f"Request blocked: {e}")
            body = e.body if hasattr(e, 'body') and isinstance(e.body, dict) else {}
            reason = body.get("message", str(e))
            raise LLMRequestBlockedError(reason) from e
        except RateLimitError as e:
            self._handle_rate_limit(e)

        if not response.choices:
            return "", []

        message = response.choices[0].message
        content = message.content or ""

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append({
                    "id": tc.id,
                    "function": {"name": tc.function.name, "arguments": args},
                })
            logger.info(f"Tool calls: {[tc['function']['name'] for tc in tool_calls]}")
        else:
            logger.info("No tool calls from LLM")

        return content, tool_calls

    async def process_tool_result(
        self,
        original_query: str,
        tool_call: dict[str, Any],
        tool_result: Any,
        system_prompt: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """Format a tool result into a human-readable response."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": original_query})

        messages.extend([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.get("id", "call_0"),
                    "type": "function",
                    "function": {
                        "name": tool_call["function"]["name"],
                        "arguments": json.dumps(tool_call["function"]["arguments"]),
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id", "call_0"),
                "content": json.dumps(tool_result) if isinstance(tool_result, (dict, list)) else str(tool_result),
            },
        ])

        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if extra_headers:
            params["extra_headers"] = extra_headers

        try:
            response = self.client.chat.completions.create(**params)
        except BadRequestError as e:
            logger.warning(f"Request blocked: {e}")
            body = e.body if hasattr(e, 'body') and isinstance(e.body, dict) else {}
            reason = body.get("message", str(e))
            raise LLMRequestBlockedError(reason) from e
        except RateLimitError as e:
            self._handle_rate_limit(e)

        return (response.choices[0].message.content or "").strip()
