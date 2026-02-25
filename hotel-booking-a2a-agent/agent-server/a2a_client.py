"""
A2A Client module using the official A2A Python SDK.

This module provides a thin wrapper around the SDK's native client for agent discovery
and communication. It leverages A2ACardResolver for agent card discovery and the SDK's
A2AClient for JSON-RPC messaging, following the A2A protocol specification.

The A2AAgentRegistry dynamically discovers remote agents, reads their skills from their
Agent Cards, and exposes them so the LLM can decide when to delegate to external agents.
"""
import logging
import uuid
from typing import Any, Optional

import httpx

from a2a.client import (
    A2ACardResolver,
    A2AClient,
    A2AClientError,
    A2AClientHTTPError,
    A2AClientJSONError,
    create_text_message_object,
)
from a2a.types import (
    AgentCard,
    Message,
    MessageSendParams,
    Role,
    SendMessageRequest,
)
from a2a.utils import get_message_text

from agent_server.colored_logger import get_agent_logger

logger = get_agent_logger(__name__)


# Re-export A2AClientError so existing imports still work
__all__ = [
    "A2ARemoteAgent",
    "A2AAgentRegistry",
    "A2AClientError",
]


class A2ARemoteAgent:
    """
    Represents a discovered remote A2A agent.

    Wraps the SDK's A2AClient and the resolved AgentCard, providing
    convenient access to the agent's skills and a simple send_message interface.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        client: A2AClient,
        httpx_client: httpx.AsyncClient,
    ):
        self.agent_card = agent_card
        self._client = client
        self._httpx_client = httpx_client

    @property
    def name(self) -> str:
        return self.agent_card.name

    @property
    def description(self) -> str:
        return self.agent_card.description or ""

    @property
    def skills(self) -> list:
        """Return the list of AgentSkill objects from the agent card."""
        return self.agent_card.skills or []

    def get_skill_descriptions(self) -> str:
        """Get a formatted string of skill descriptions discovered from the agent card."""
        if not self.skills:
            return "No skills available"

        descriptions = []
        for skill in self.skills:
            skill_name = skill.name if hasattr(skill, "name") else str(skill)
            skill_desc = (
                skill.description
                if hasattr(skill, "description")
                else "No description"
            )
            tags = skill.tags if hasattr(skill, "tags") else []
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            descriptions.append(f"  - {skill_name}: {skill_desc}{tag_str}")

        return "\n".join(descriptions)

    def has_skill_matching(self, *keywords: str) -> bool:
        """
        Check if this agent has a skill matching any of the given keywords.
        Searches in skill names, descriptions, and tags.
        """
        for skill in self.skills:
            searchable = " ".join(
                [
                    getattr(skill, "name", ""),
                    getattr(skill, "description", ""),
                    " ".join(getattr(skill, "tags", [])),
                ]
            ).lower()
            for keyword in keywords:
                if keyword.lower() in searchable:
                    return True
        return False

    async def send_message(self, text: str) -> str:
        """
        Send a text message to the remote agent and return the response text.

        Uses the SDK's native A2AClient.send_message with proper A2A types.

        Args:
            text: The message text to send.

        Returns:
            The agent's response as a string.
        """
        # Build the request using SDK helpers
        message = create_text_message_object(role=Role.user, content=text)
        send_params = MessageSendParams(message=message)
        request = SendMessageRequest(id=str(uuid.uuid4()), params=send_params)

        logger.info(f"[A2A] Sending to '{self.name}': {text[:100]}...")

        response = await self._client.send_message(request)

        # Extract text from the SDK response
        return self._extract_response_text(response)

    def _extract_response_text(self, response: Any) -> str:
        """Extract text from the SDK SendMessageResponse."""
        try:
            result = response.root
            # result is a SendMessageSuccessResponse or JSONRPCErrorResponse
            if hasattr(result, "error") and result.error:
                error_msg = (
                    result.error.message
                    if hasattr(result.error, "message")
                    else str(result.error)
                )
                raise A2AClientError(f"Agent error: {error_msg}")

            # result.result is either a Task or a Message
            inner = result.result if hasattr(result, "result") else result

            # If it's a Message, use SDK's get_message_text
            if isinstance(inner, Message):
                return get_message_text(inner) or "No response received"

            # If it's a Task, look for the final message in status
            if hasattr(inner, "status") and inner.status:
                status_msg = inner.status.message
                if status_msg:
                    return get_message_text(status_msg) or "No response received"

            # Fallback: try to find parts in the result
            if hasattr(inner, "parts") and inner.parts:
                for part in inner.parts:
                    part_obj = part.root if hasattr(part, "root") else part
                    if hasattr(part_obj, "text"):
                        return part_obj.text

            return str(inner) if inner else "No response received"
        except A2AClientError:
            raise
        except Exception as e:
            logger.warning(f"Failed to parse agent response: {e}")
            return str(response) if response else "No response received"

    async def cleanup(self) -> None:
        """Clean up resources (httpx_client lifecycle is managed by the registry)."""
        pass


class A2AAgentRegistry:
    """
    Registry for discovering and managing remote A2A agents.

    Uses the SDK's A2ACardResolver to discover agents from their well-known
    URLs, then creates SDK-native A2AClient instances for communication.

    This is the central point for dynamic agent discovery: rather than
    hard-coding knowledge about what remote agents can do, the registry
    discovers their capabilities from their Agent Cards at runtime.
    """

    def __init__(self) -> None:
        self._agents: dict[str, A2ARemoteAgent] = {}
        self._httpx_client: Optional[httpx.AsyncClient] = None

    def _get_httpx_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._httpx_client is None or self._httpx_client.is_closed:
            self._httpx_client = httpx.AsyncClient(timeout=60.0)
        return self._httpx_client

    async def discover_agent(
        self, base_url: str, name: Optional[str] = None
    ) -> Optional[A2ARemoteAgent]:
        """
        Discover a remote agent by resolving its Agent Card from a base URL.

        This follows the A2A protocol discovery mechanism:
        1. Fetch the Agent Card from {base_url}/.well-known/agent-card.json
        2. Parse the card to learn the agent's name, skills, and capabilities
        3. Create an SDK-native client to communicate with it

        Args:
            base_url: The base URL of the agent (e.g., "http://host:8082/currency-agent").
                      The /.well-known/agent-card.json path is appended automatically by the SDK.
            name: Optional friendly name. If not provided, uses the name from the Agent Card.

        Returns:
            The discovered A2ARemoteAgent, or None if discovery failed.
        """
        httpx_client = self._get_httpx_client()

        try:
            # Step 1: Resolve the Agent Card using SDK's A2ACardResolver
            logger.info(f"[A2A Discovery] Resolving agent card from: {base_url}")
            resolver = A2ACardResolver(
                httpx_client=httpx_client,
                base_url=base_url,
            )
            agent_card: AgentCard = await resolver.get_agent_card()

            agent_name = name or agent_card.name
            logger.info(f"[A2A Discovery] Discovered agent: '{agent_name}'")
            logger.info(f"[A2A Discovery]   Description: {agent_card.description}")
            logger.info(f"[A2A Discovery]   Version: {agent_card.version}")
            logger.info(f"[A2A Discovery]   URL: {agent_card.url}")

            # Log discovered skills
            if agent_card.skills:
                for skill in agent_card.skills:
                    s_name = skill.name if hasattr(skill, "name") else "?"
                    s_desc = skill.description if hasattr(skill, "description") else ""
                    s_tags = skill.tags if hasattr(skill, "tags") else []
                    logger.info(
                        f"[A2A Discovery]   Skill: {s_name} — {s_desc} {s_tags}"
                    )
            else:
                logger.info("[A2A Discovery]   No skills declared")

            # Step 2: Create the SDK's native A2AClient for JSON-RPC communication
            sdk_client = A2AClient(
                httpx_client=httpx_client,
                agent_card=agent_card,
            )

            # Step 3: Build the wrapper and register it
            remote_agent = A2ARemoteAgent(
                agent_card=agent_card,
                client=sdk_client,
                httpx_client=httpx_client,
            )
            self._agents[agent_name] = remote_agent

            logger.info(
                f"[A2A Discovery] Agent '{agent_name}' registered successfully"
            )
            return remote_agent

        except (A2AClientHTTPError, A2AClientJSONError) as e:
            logger.warning(
                f"[A2A Discovery] Failed to discover agent at {base_url}: {e}"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[A2A Discovery] Unexpected error discovering agent at {base_url}: {e}"
            )
            return None

    def get_agent(self, name: str) -> Optional[A2ARemoteAgent]:
        """Get a discovered agent by name."""
        return self._agents.get(name)

    def find_agent_with_skill(self, *keywords: str) -> Optional[A2ARemoteAgent]:
        """
        Find the first agent that has a skill matching any of the given keywords.

        This is the key to dynamic delegation: instead of hard-coding which agent
        to call for currency conversion, the hotel booking agent can search for
        any agent that has a skill matching "currency", "exchange", etc.

        Args:
            *keywords: Keywords to search for in skill names, descriptions, and tags.

        Returns:
            The first matching agent, or None if no agent matches.
        """
        for agent in self._agents.values():
            if agent.has_skill_matching(*keywords):
                logger.info(
                    f"[A2A] Found agent '{agent.name}' matching skills: {keywords}"
                )
                return agent
        return None

    def list_agents(self) -> list[str]:
        """List all discovered agent names."""
        return list(self._agents.keys())

    def get_all_agents(self) -> list[A2ARemoteAgent]:
        """Get all discovered agents."""
        return list(self._agents.values())

    def get_discovered_skills_description(self) -> str:
        """
        Get a formatted description of ALL discovered agents and their skills.

        This is designed to be injected into the LLM system prompt, so the LLM
        can understand what external capabilities are available and when to
        suggest delegation.
        """
        if not self._agents:
            return ""

        lines = [
            "\nAVAILABLE EXTERNAL AGENTS (discovered via A2A protocol):"
        ]
        for _name, agent in self._agents.items():
            lines.append(f"\n• Agent: {agent.name}")
            lines.append(f"  Description: {agent.description}")
            lines.append(f"  Skills:")
            lines.append(agent.get_skill_descriptions())

        return "\n".join(lines)

    async def cleanup(self) -> None:
        """Clean up all agent connections and the shared HTTP client."""
        self._agents.clear()
        if self._httpx_client and not self._httpx_client.is_closed:
            await self._httpx_client.aclose()
            self._httpx_client = None
