import asyncio
import os
import sys
import json
import logging
from typing import Dict, Any, List, Optional

from ollama import Client as OllamaClient, ChatResponse, ResponseError
from dotenv import load_dotenv

# Add the agent_server package to Python path to access colored_logger
sys.path.insert(0, '/app/agent-server')

try:
    from agent_server.colored_logger import get_llm_logger
    logger = get_llm_logger(__name__)
except ImportError:
    # Fallback to standard logging if colored_logger is not available
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning("Colored logger not available, using standard logging")

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://gio-apim-gateway:8082/llm")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:0.6b")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))

class LLMClient:
    """LLM Client for handling interactions with Ollama."""
    
    def __init__(self, 
                 ollama_url: Optional[str] = None,
                 model: Optional[str] = None,
                 temperature: Optional[float] = None):
        self.ollama = OllamaClient(host=ollama_url or OLLAMA_URL)
        self.model = model or OLLAMA_MODEL
        self.temperature = temperature or OLLAMA_TEMPERATURE

    async def process_query(self, 
                           query: str, 
                           available_tools: List[Dict[str, Any]],
                           system_prompt: Optional[str] = None) -> tuple[str, List[Dict[str, Any]]]:
        """
        Process a query using the LLM with available tools.
        
        Args:
            query: The user query to process
            available_tools: List of available tools for the LLM
            system_prompt: Optional system prompt to guide the LLM behavior
            
        Returns: (response_content, tool_calls)
        """
        
        logger.info(f"Processing query with LLM (model: {self.model})")
        logger.debug(f"User query: {query}")
        logger.debug(f"Available tools: {len(available_tools)}")
        if system_prompt:
            logger.debug(f"System prompt: {system_prompt[:100]}..." if len(system_prompt) > 100 else f"System prompt: {system_prompt}")
        
        messages = [
            {"role": "user", "content": query}
        ]
        
        # Add system prompt if provided
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        try:
            response: ChatResponse = self.ollama.chat(
                model=self.model,
                messages=messages,
                tools=available_tools,
                options={"temperature": self.temperature},
                stream=False
            )

            initial_content = (response.get('message') or {}).get('content', "")
            tool_calls = (response.get('message') or {}).get('tool_calls') or []

            if tool_calls:
                logger.info(f"LLM decided to call {len(tool_calls)} tool(s)")
                for idx, tool_call in enumerate(tool_calls):
                    tool_name = tool_call.get("function", {}).get("name", "unknown")
                    tool_args = tool_call.get("function", {}).get("arguments", {})
                    logger.info(f"Tool call #{idx+1}: {tool_name}")
                    logger.debug(f"Tool arguments: {json.dumps(tool_args, indent=2)}")
            else:
                logger.info("LLM decided not to call any tools")
                if initial_content:
                    logger.debug(f"Initial response: {initial_content[:200]}..." if len(initial_content) > 200 else f"Initial response: {initial_content}")

            return initial_content, tool_calls
        
        except ResponseError as e:
            logger.error(f'Ollama ResponseError: {e.error}')
            raise Exception(f"Failed to process query: {e.error}") from e
        except Exception as e:
            logger.error(f'Unexpected error in process_query: {str(e)}', exc_info=True)
            raise Exception(f"Failed to process query: {str(e)}") from e

    async def process_tool_result(self, 
                                  original_query: str, 
                                  tool_call: Dict[str, Any], 
                                  tool_result: Any,
                                  system_prompt: Optional[str] = None) -> str:
        """
        Process the result of a tool call and generate final response.
        
        Args:
            original_query: The original user query
            tool_call: The tool call that was executed
            tool_result: The result from the tool execution
            system_prompt: Optional system prompt to guide the LLM behavior
            
        Returns: Final response string
        """
        
        tool_name = tool_call.get("function", {}).get("name", "unknown")
        logger.info(f"Processing tool result from '{tool_name}' to generate final response")
        logger.debug(f"Tool result: {json.dumps(tool_result, indent=2) if isinstance(tool_result, (dict, list)) else str(tool_result)}")
        
        followup_messages = [
            {"role": "user", "content": original_query},
            {
                "role": "assistant",
                "tool_calls": [tool_call]
            },
            {
                "role": "tool",
                "tool_use_id": tool_call.get("id"),
                "content": str(tool_result)
            }
        ]
        
        # Add system prompt if provided
        if system_prompt:
            followup_messages.insert(0, {"role": "system", "content": system_prompt})

        try:
            followup_response = self.ollama.chat(
                model=self.model,
                messages=followup_messages,
                options={"temperature": self.temperature},
                stream=False
            )

            final_response = followup_response['message']['content'].strip()
            logger.info("Generated final response from LLM")
            logger.debug(f"Final response: {final_response[:300]}..." if len(final_response) > 300 else f"Final response: {final_response}")
            
            return final_response
        
        except ResponseError as e:
            logger.error(f'Ollama ResponseError: {e.error}')
            raise Exception(f"Failed to process tool result: {e.error}") from e
        except Exception as e:
            logger.error(f'Unexpected error in process_tool_result: {str(e)}', exc_info=True)
            raise Exception(f"Failed to process tool result: {str(e)}") from e

async def main_async():
    """Main async function for LLM Client."""
    client = LLMClient()
    
    # Simple test query
    test_query = "Hello, I need help with hotel bookings"
    available_tools = []  # No tools for basic test
    
    try:
        response, tool_calls = await client.process_query(test_query, available_tools)
        
        print("LLM Client Test:")
        print(f"Query: {test_query}")
        print(f"Response: {response}")
        print(f"Tool calls requested: {len(tool_calls)}")
        
        print("\nLLM Client is ready for integration.")
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
    
    return 0

def main():
    """Main entry point for LLM Client."""
    return asyncio.run(main_async())

if __name__ == "__main__":
    sys.exit(main())
