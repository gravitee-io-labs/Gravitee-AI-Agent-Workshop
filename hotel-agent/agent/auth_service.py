"""Authentication service using OAuth 2.0 Token Exchange (RFC 8693)."""
import base64
import logging
from typing import Optional, Tuple

import httpx

from agent.logger import get_agent_logger

logger = get_agent_logger(__name__)


class AuthenticationError(Exception):
    pass


class AuthService:
    """Exchanges a user's access token for a delegated agent token via RFC 8693.

    Flow:
      1. The web app authenticates the user and sends their access token.
      2. The agent exchanges that token at the AM token endpoint using
         grant_type=urn:ietf:params:oauth:grant-type:token-exchange.
      3. AM returns a new token scoped to the agent, acting on behalf of the user.
    """

    def __init__(self, am_token_url: str, am_client_id: str, am_client_secret: str):
        self.am_token_url = am_token_url
        self.am_client_id = am_client_id
        self.am_client_secret = am_client_secret
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def initialize(self):
        logger.info(f"AuthService ready (token exchange endpoint: {self.am_token_url})")

    async def exchange_token(self, subject_token: str) -> str:
        """Exchange a user access token for a delegated agent token (RFC 8693).

        Args:
            subject_token: The user's access token (from the web app).

        Returns:
            A new access token issued to the agent on behalf of the user.
        """
        try:
            credentials = f"{self.am_client_id}:{self.am_client_secret}"
            basic_auth = base64.b64encode(credentials.encode()).decode()

            response = await self._http_client.post(
                self.am_token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {basic_auth}",
                },
            )
            response.raise_for_status()

            token_response = response.json()
            access_token = token_response.get("access_token")
            if not access_token:
                raise AuthenticationError("No access_token in token exchange response")

            logger.info("Token exchange successful")
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
