"""
Currency Converter A2A Agent using the official A2A SDK.
This agent converts currencies using the Frankfurter API.
"""
import logging
import os
from collections.abc import AsyncIterable
from typing import Any, Literal

import click
import httpx
import uvicorn
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InvalidParamsError,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.utils import new_agent_text_message, new_task


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

memory = MemorySaver()


def str2bool(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


@tool
def get_exchange_rate(
    currency_from: str = 'USD',
    currency_to: str = 'EUR',
    currency_date: str = 'latest',
    amount: float = 1.0,
):
    """Use this to get current exchange rate and optionally convert an amount.

    Args:
        currency_from: The currency to convert from (e.g., "USD", "EUR").
        currency_to: The currency to convert to (e.g., "EUR", "USD").
        currency_date: The date for the exchange rate or "latest". Defaults to "latest".
        amount: The amount of currency to convert. Defaults to 1.0.

    Returns:
        A dictionary containing the exchange rate, the converted amount, and metadata.
    """
    try:
        response = httpx.get(
            f'https://api.frankfurter.app/{currency_date}',
            params={'from': currency_from, 'to': currency_to, 'amount': amount},
        )
        response.raise_for_status()

        data = response.json()
        if 'rates' not in data:
            return {'error': 'Invalid API response format.'}
        return data
    except httpx.HTTPError as e:
        return {'error': f'API request failed: {e}'}
    except ValueError:
        return {'error': 'Invalid JSON response from API.'}


class ResponseFormat(BaseModel):
    """Respond to the user in this format."""
    status: Literal['input_required', 'completed', 'error'] = 'input_required'
    message: str


class CurrencyAgentExecutor(AgentExecutor):
    """Currency agent executor using LangGraph."""

    SYSTEM_INSTRUCTION = (
        'You are a specialized assistant for currency conversions. '
        "Your sole purpose is to use the 'get_exchange_rate' tool to answer questions about currency exchange rates. "
        'You can handle queries like "how much is 200 euros in dollars" or "convert 500 USD to GBP". '
        'When the user specifies an amount, pass it as the "amount" parameter to the tool. '
        'When the user does not specify an amount, default to 1.0 to show the exchange rate. '
        'Always present the result clearly, e.g. "200 EUR = 215.40 USD (rate: 1.077)". '
        'If the user asks about anything other than currency conversion or exchange rates, '
        'politely state that you cannot help with that topic and can only assist with currency-related queries. '
        'Do not attempt to answer unrelated questions or use tools for other purposes. '
        'Set response status to input_required if the user needs to provide more information. '
        'Set response status to error if there is an error while processing the request. '
        'Set response status to completed if the request is complete.'
    )

    def __init__(self, streaming: bool = True):
        self.streaming = streaming
        self.model = ChatGoogleGenerativeAI(model='gemini-2.0-flash')
        self.tools = [get_exchange_rate]
        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=self.SYSTEM_INSTRUCTION,
            response_format=ResponseFormat,
        )

    def _get_user_query(self, context: RequestContext) -> str:
        """Extract text from the user's message parts."""
        message = context.message
        if message and message.parts:
            for part in message.parts:
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    return part.root.text
                if hasattr(part, 'text'):
                    return part.text
        return ""

    def _get_session_id(self, context: RequestContext) -> str:
        """Get or generate a session ID for the conversation."""
        if context.task_id:
            return context.task_id
        if context.context_id:
            return context.context_id
        return "default-session"

    async def execute(
        self,
        context: RequestContext,
        event_queue: Any,
    ) -> None:
        """Execute the agent logic for non-streaming requests."""
        query = self._get_user_query(context)
        session_id = self._get_session_id(context)

        try:
            config = {'configurable': {'thread_id': session_id}}
            self.graph.invoke({'messages': [('user', query)]}, config)
            response = self._get_agent_response(config)

            if response['is_task_complete']:
                state = TaskState.completed
            elif response['require_user_input']:
                state = TaskState.input_required
            else:
                state = TaskState.working

            # Create a text message response
            message = new_agent_text_message(response['content'])
            await event_queue.enqueue_event(message)

        except Exception as e:
            logger.error(f'Error executing agent: {e}')
            error_message = new_agent_text_message(
                f'An error occurred: {str(e)}'
            )
            await event_queue.enqueue_event(error_message)

    async def cancel(self, context: RequestContext, event_queue: Any) -> None:
        """Handle task cancellation."""
        logger.info(f'Task cancelled: {context.task_id}')

    def _get_agent_response(self, config) -> dict:
        """Get the structured response from the agent."""
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get('structured_response')
        if structured_response and isinstance(structured_response, ResponseFormat):
            if structured_response.status in ('input_required', 'error'):
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            if structured_response.status == 'completed':
                return {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': structured_response.message,
                }

        return {
            'is_task_complete': False,
            'require_user_input': True,
            'content': 'Unable to process your request. Please try again.',
        }


def create_agent_card(host: str, port: int, streaming: bool) -> AgentCard:
    """Create the agent card describing this agent's capabilities."""
    capabilities = AgentCapabilities(
        streaming=streaming,
        pushNotifications=False,
    )

    skill = AgentSkill(
        id='convert_currency',
        name='Currency Exchange Rates Tool',
        description='Helps with exchange values between various currencies',
        tags=['currency conversion', 'currency exchange'],
        examples=['What is exchange rate between USD and GBP?'],
    )

    return AgentCard(
        name='Currency Agent',
        description='Helps with exchange rates for currencies',
        url=f'http://{host}:{port}/',
        version='1.0.0',
        protocolVersions=['1.0'],
        defaultInputModes=['text/plain'],
        defaultOutputModes=['text/plain'],
        capabilities=capabilities,
        skills=[skill],
    )


@click.command()
@click.option('--host', 'host', default='0.0.0.0')
@click.option('--port', 'port', default=10000)
def main(host: str, port: int):
    """Starts the Currency Agent server using the official A2A SDK."""
    try:
        if not os.getenv('GOOGLE_API_KEY'):
            raise ValueError('GOOGLE_API_KEY environment variable not set.')

        streaming = str2bool(os.getenv('STREAMING', 'true'))
        agent_card = create_agent_card(host, port, streaming)

        # Create the agent executor
        agent_executor = CurrencyAgentExecutor(streaming=streaming)

        # Create task store
        task_store = InMemoryTaskStore()

        # Create request handler
        request_handler = DefaultRequestHandler(
            agent_executor=agent_executor,
            task_store=task_store,
        )

        # Create the A2A application
        app = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler,
        )

        logger.info(f'Starting Currency Agent server on {host}:{port}')
        logger.info(f'Streaming enabled: {streaming}')
        logger.info(f'Agent card available at: http://{host}:{port}/.well-known/agent-card.json')

        uvicorn.run(app.build(), host=host, port=port)

    except Exception as e:
        logger.error(f'Error starting server: {e}')
        raise


if __name__ == '__main__':
    main()
