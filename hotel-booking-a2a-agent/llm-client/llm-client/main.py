import asyncio
import os
import sys
import json
import logging
from typing import Any

from openai import OpenAI
from dotenv import load_dotenv

# Add the agent-server package to Python path to access colored_logger
sys.path.insert(0, '/app/hotel-booking-a2a-agent/agent-server')

try:
    from agent_server.colored_logger import get_llm_logger
    logger = get_llm_logger(__name__)
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

load_dotenv()

# LLM API configuration
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://gio-apim-gateway:8082/llm/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:0.6b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))


class LLMClient:
    """LLM Client using OpenAI-compatible API."""
    
    def __init__(self, 
                 base_url: str | None = None,
                 api_key: str | None = None,
                 model: str | None = None,
                 temperature: float | None = None):
        self.client = OpenAI(
            base_url=base_url or LLM_BASE_URL,
            api_key=api_key or LLM_API_KEY
        )
        self.model = model or LLM_MODEL
        self.temperature = temperature or LLM_TEMPERATURE

    async def process_query(
        self, 
        query: str, 
        available_tools: list[dict[str, Any]],
        system_prompt: str | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Process a query using the LLM with available tools."""
        
        logger.info(f"Processing query with LLM (model: {self.model})")
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": query})

        # Build request parameters
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if available_tools:
            params["tools"] = available_tools
            params["tool_choice"] = "auto"
        
        response = self.client.chat.completions.create(**params)
        
        # Handle empty response
        if not response.choices:
            logger.warning("LLM returned empty choices")
            return "", []

        message = response.choices[0].message
        content = message.content or ""
        
        # Extract tool calls
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
                    "function": {
                        "name": tc.function.name,
                        "arguments": args
                    }
                })
            logger.info(f"LLM decided to call {len(tool_calls)} tool(s): {[tc['function']['name'] for tc in tool_calls]}")
        else:
            logger.info("LLM returned direct response (no tool calls)")

        return content, tool_calls

    async def process_tool_result(
        self, 
        original_query: str, 
        tool_call: dict[str, Any], 
        tool_result: Any,
        system_prompt: str | None = None
    ) -> str:
        """Process tool result and generate final response."""
        
        tool_name = tool_call.get("function", {}).get("name", "unknown")
        logger.info(f"Processing result from tool '{tool_name}'")
        
        # Build conversation with tool result
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.extend([
            {"role": "user", "content": original_query},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.get("id", "call_0"),
                    "type": "function",
                    "function": {
                        "name": tool_call["function"]["name"],
                        "arguments": json.dumps(tool_call["function"]["arguments"])
                    }
                }]
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id", "call_0"),
                "content": json.dumps(tool_result) if isinstance(tool_result, (dict, list)) else str(tool_result)
            }
        ])

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )

        final_response = response.choices[0].message.content or ""
        logger.info("Generated final response from LLM")
        
        return final_response.strip()


async def main_async():
    """Test the LLM Client."""
    client = LLMClient()
    
    try:
        response, tool_calls = await client.process_query(
            "Hello, I need help with hotel bookings",
            available_tools=[]
        )
        print(f"Response: {response}")
        print(f"Tool calls: {len(tool_calls)}")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
