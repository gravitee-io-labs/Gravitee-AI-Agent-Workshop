"""Authentication service using OAuth 2.0 Token Exchange (RFC 8693) with delegation."""
import base64
import time
from typing import Optional

import httpx

from agent.logger import get_agent_logger

logger = get_agent_logger(__name__)

TOKEN_EXPIRY_MARGIN_SECS = 30


class AuthenticationError(Exception):
    pass


class AuthService:
    """Manages agent token lifecycle and RFC 8693 token exchange (delegation).

    - Agent token obtained via client_credentials, auto-refreshed before expiry.
    - User requests trigger token exchange: user token (subject) + agent token (actor)
      → delegated token with `act` claim.
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
        self._agent_token_expires_at: float = 0

    @property
    def agent_token(self) -> Optional[str]:
        return self._agent_token

    def _is_agent_token_expired(self) -> bool:
        return time.time() >= (self._agent_token_expires_at - TOKEN_EXPIRY_MARGIN_SECS)

    async def initialize(self):
        logger.info(f"AuthService initializing (endpoint: {self.am_token_url})")
        await self._refresh_agent_token()
        logger.info("AuthService ready — agent token acquired")

    async def ensure_agent_token(self) -> str:
        """Return a valid agent token, refreshing if expired."""
        if not self._agent_token or self._is_agent_token_expired():
            logger.info("Agent token expired or missing, refreshing...")
            await self._refresh_agent_token()
        return self._agent_token

    async def _refresh_agent_token(self):
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
            data = response.json()
            self._agent_token = data.get("access_token")
            if not self._agent_token:
                raise AuthenticationError("No access_token in client_credentials response")
            expires_in = data.get("expires_in", 3600)
            self._agent_token_expires_at = time.time() + expires_in
            logger.info(f"Agent token refreshed (expires in {expires_in}s)")
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to obtain agent token: {e.response.status_code} — {e.response.text}")
            raise AuthenticationError(f"Failed to obtain agent token: {e}")
        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"Failed to obtain agent token: {e}")
            raise AuthenticationError(f"Failed to obtain agent token: {e}")

    async def exchange_token(self, subject_token: str) -> str:
        """Exchange a user token for a delegated token (RFC 8693).

        Uses the agent's own token as actor_token, ensuring it's fresh.
        """
        agent_token = await self.ensure_agent_token()

        try:
            response = await self._http_client.post(
                self.am_token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "actor_token": agent_token,
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
        except AuthenticationError:
            raise
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
