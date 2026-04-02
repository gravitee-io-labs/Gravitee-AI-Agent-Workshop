"""Authentication service for OAuth2/OIDC operations."""
import base64
import logging
from typing import Optional, Tuple

import httpx

from agent.logger import get_agent_logger

logger = get_agent_logger(__name__)


class AuthenticationError(Exception):
    pass


class AuthService:
    """Handles OAuth2/OIDC authentication for tool calls."""

    def __init__(self, oidc_discovery_url: str, am_token_url: str,
                 am_client_id: str, am_client_secret: str):
        self.oidc_discovery_url = oidc_discovery_url
        self.am_token_url = am_token_url
        self.am_client_id = am_client_id
        self.am_client_secret = am_client_secret
        self.userinfo_endpoint: Optional[str] = None
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def initialize(self):
        try:
            response = await self._http_client.get(self.oidc_discovery_url)
            response.raise_for_status()
            oidc_config = response.json()
            self.userinfo_endpoint = oidc_config.get("userinfo_endpoint")
            if not self.userinfo_endpoint:
                raise AuthenticationError("userinfo_endpoint not found in OIDC config")
            logger.info(f"OIDC discovery complete: {self.userinfo_endpoint}")
        except Exception as e:
            logger.error(f"OIDC discovery failed: {e}")
            raise AuthenticationError(f"OIDC discovery failed: {e}")

    async def get_user_email_from_token(self, access_token: str) -> str:
        if not self.userinfo_endpoint:
            raise AuthenticationError("AuthService not initialized")
        try:
            response = await self._http_client.get(
                self.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            email = response.json().get("email")
            if not email:
                raise AuthenticationError("Email not found in userinfo response")
            return email
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Invalid or expired access token")
            raise AuthenticationError(f"Failed to retrieve user info: {e}")

    async def get_am_access_token(self, email: str) -> str:
        try:
            credentials = f"{self.am_client_id}:{self.am_client_secret}"
            basic_auth = base64.b64encode(credentials.encode()).decode()
            response = await self._http_client.post(
                self.am_token_url,
                data={"grant_type": "client_credentials"},
                headers={
                    "sub-email": email,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {basic_auth}",
                },
            )
            response.raise_for_status()
            token = response.json().get("access_token")
            if not token:
                raise AuthenticationError("No access_token in AM response")
            return token
        except httpx.HTTPStatusError as e:
            raise AuthenticationError(f"Failed to get AM token: {e}")

    async def process_authorization_for_tool(self, authorization_header: Optional[str]) -> Tuple[str, str]:
        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise AuthenticationError("Missing or invalid Authorization header")
        access_token = authorization_header[7:].strip()
        if not access_token:
            raise AuthenticationError("Empty access token")
        email = await self.get_user_email_from_token(access_token)
        am_token = await self.get_am_access_token(email)
        return am_token, email

    async def cleanup(self):
        try:
            await self._http_client.aclose()
        except Exception as e:
            logger.error(f"AuthService cleanup error: {e}")
