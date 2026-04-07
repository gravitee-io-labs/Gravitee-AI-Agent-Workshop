"""Authentication service using OAuth 2.0 Token Exchange (RFC 8693) with delegation."""
import base64
from typing import Optional

import httpx

from agent.logger import get_agent_logger

logger = get_agent_logger(__name__)


class AuthenticationError(Exception):
    pass


class AuthService:
    """Exchanges a user's access token for a delegated agent token via RFC 8693.

    Delegation flow (RFC 8693 §1.1):
      1. On startup, the agent obtains its own access token via client_credentials.
         This token represents the agent's identity (the "actor").
      2. When a user request arrives, the agent exchanges the user's token
         (subject_token) together with its own token (actor_token).
      3. AM returns a delegated token with an `act` claim, proving the agent
         acts on behalf of the user — not impersonating them.
    """

    def __init__(self, am_token_url: str, am_client_id: str, am_client_secret: str):
        self.am_token_url = am_token_url
        self.am_client_id = am_client_id
        self.am_client_secret = am_client_secret
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._basic_auth = base64.b64encode(
            f"{am_client_id}:{am_client_secret}".encode()
        ).decode()
        self._agent_token: Optional[str] = None

    @property
    def agent_token(self) -> Optional[str]:
        """The agent's own access token (obtained via client_credentials)."""
        return self._agent_token

    async def initialize(self):
        """Obtain the agent's own access token via client_credentials."""
        logger.info(f"AuthService initializing (endpoint: {self.am_token_url})")
        await self._refresh_agent_token()
        logger.info("AuthService ready — agent token acquired (actor for delegation)")

    async def _refresh_agent_token(self):
        """Get or refresh the agent's own token via client_credentials grant."""
        try:
            response = await self._http_client.post(
                self.am_token_url,
                data={"grant_type": "client_credentials"},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {self._basic_auth}",
                },
            )
            response.raise_for_status()
            self._agent_token = response.json().get("access_token")
            if not self._agent_token:
                raise AuthenticationError("No access_token in client_credentials response")
            logger.info("Agent token (actor) refreshed successfully")
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to obtain agent token: {e.response.status_code} — {e.response.text}")
            raise AuthenticationError(f"Failed to obtain agent token: {e}")
        except Exception as e:
            logger.error(f"Failed to obtain agent token: {e}")
            raise AuthenticationError(f"Failed to obtain agent token: {e}")

    async def exchange_token(self, subject_token: str) -> str:
        """Exchange a user access token for a delegated token (RFC 8693).

        Sends both subject_token (user) and actor_token (agent) to signal
        delegation. The resulting token carries an `act` claim identifying
        the agent as the acting party.

        Args:
            subject_token: The user's access token (from the web app).

        Returns:
            A delegated access token issued on behalf of the user.
        """
        if not self._agent_token:
            await self._refresh_agent_token()

        try:
            response = await self._http_client.post(
                self.am_token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "actor_token": self._agent_token,
                    "actor_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {self._basic_auth}",
                },
            )
            response.raise_for_status()

            token_response = response.json()
            access_token = token_response.get("access_token")
            if not access_token:
                raise AuthenticationError("No access_token in token exchange response")

            logger.info("Token exchange successful (delegation)")
            return access_token

        except httpx.HTTPStatusError as e:
            logger.error(f"Token exchange failed: {e.response.status_code} — {e.response.text}")
            raise AuthenticationError(f"Token exchange failed: {e}")
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            raise AuthenticationError(f"Token exchange error: {e}")

    async def process_authorization_for_tool(self, authorization_header: Optional[str]) -> str:
        """Extract Bearer token and exchange it. Returns the delegated token."""
        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise AuthenticationError("Missing or invalid Authorization header")
        subject_token = authorization_header[7:].strip()
        if not subject_token:
            raise AuthenticationError("Empty access token")
        return await self.exchange_token(subject_token)

    async def cleanup(self):
        try:
            await self._http_client.aclose()
        except Exception as e:
            logger.error(f"AuthService cleanup error: {e}")
