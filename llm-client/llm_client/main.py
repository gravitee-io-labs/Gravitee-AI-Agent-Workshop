import asyncio
import os
import sys
from typing import Dict, Any, List, Optional

from ollama import Client as OllamaClient, ChatResponse, ResponseError
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
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
        
    def get_system_instructions(self) -> str:
        """Get system instructions for hotel booking assistant."""
        return (
            "You are an Hotel Booking AI Agent whose only role is to use the tools provided.\n"
            "Always strictly follow this rule."
        )

    async def process_query(self, query: str, available_tools: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
        """
        Process a query using the LLM with available tools.
        Returns: (response_content, tool_calls)
        """
        system_instructions = self.get_system_instructions()
        
        messages = [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": query}
        ]

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

            return initial_content, tool_calls
        
        except ResponseError as e:
            print(f'Ollama ResponseError: {e.error}')
            raise Exception(f"Failed to process query: {e.error}") from e
        except Exception as e:
            print(f'Unexpected error in process_query: {str(e)}')
            raise Exception(f"Failed to process query: {str(e)}") from e

    async def process_tool_result(self, 
                                  original_query: str, 
                                  tool_call: Dict[str, Any], 
                                  tool_result: Any) -> str:
        """
        Process the result of a tool call and generate final response.
        """
        system_instructions = self.get_system_instructions()
        
        followup_messages = [
            {"role": "system", "content": system_instructions},
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

        try:
            followup_response = self.ollama.chat(
                model=self.model,
                messages=followup_messages,
                options={"temperature": self.temperature},
                stream=False
            )

            return followup_response['message']['content'].strip()
        
        except ResponseError as e:
            print(f'Ollama ResponseError: {e.error}')
            raise Exception(f"Failed to process tool result: {e.error}") from e
        except Exception as e:
            print(f'Unexpected error in process_tool_result: {str(e)}')
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
