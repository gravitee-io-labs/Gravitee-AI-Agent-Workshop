"""Blueprint Agent authentication.

This agent is registered in Gravitee AM as a HOSTED_DELEGATED Blueprint
Agent application. Each running instance proves itself at the token
endpoint with a `jwt-bearer` client assertion (RFC 7523), signed with a
private key loaded from a shared volume. The matching public JWK lives
on the blueprint's `settings.oauth.jwks`.

The minted access token's `act` claim records the blueprint as the
actor, and a subsequent RFC 8693 token exchange adds the user as the
subject — producing a `user → instance → blueprint` actor chain that
downstream services can audit.
"""
import os
import time
import uuid
from typing import Optional

import httpx
import jwt

from agent.logger import get_agent_logger

logger = get_agent_logger(__name__)

TOKEN_EXPIRY_MARGIN_SECS = 30
ASSERTION_VALIDITY_SECS = 300

# Demo switch: when false, the token exchange request omits actor_token, so AM
# treats it as impersonation rather than delegation. With the blueprint app
# configured allowImpersonation=false/allowDelegation=true, AM rejects it —
# useful for showing the difference between the two modes.
INCLUDE_ACTOR_TOKEN = os.getenv("INCLUDE_ACTOR_TOKEN", "true").lower() == "true"


class AuthenticationError(Exception):
    pass


class AuthService:
    """Manages agent token lifecycle and RFC 8693 token exchange (delegation).

    - Per-call `jwt-bearer` client assertions authenticate this instance to AM.
    - Agent token obtained via `client_credentials`, auto-refreshed before expiry.
    - User requests trigger token exchange: user token (subject) + agent token (actor)
      → delegated token whose nested `act` chain carries instance + blueprint.
    """

    def __init__(
        self,
        am_token_url: str,
        am_client_id: str,
        private_key_path: str,
        kid: str,
        instance_id: Optional[str] = None,
    ):
        self.am_token_url = am_token_url
        self.am_client_id = am_client_id
        self.kid = kid
        self.instance_id = instance_id or f"hotel-agent-{uuid.uuid4()}"
        self._private_key_pem: Optional[bytes] = None
        self._private_key_path = private_key_path
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._agent_token: Optional[str] = None
        self._agent_token_expires_at: float = 0

    @property
    def agent_token(self) -> Optional[str]:
        return self._agent_token

    def _is_agent_token_expired(self) -> bool:
        return time.time() >= (self._agent_token_expires_at - TOKEN_EXPIRY_MARGIN_SECS)

    def _load_private_key(self) -> bytes:
        if self._private_key_pem is None:
            with open(self._private_key_path, "rb") as fh:
                self._private_key_pem = fh.read()
        return self._private_key_pem

    def _build_client_assertion(self) -> str:
        """Sign a fresh jwt-bearer client assertion for this instance.

        `iss != sub` tells AM this is agent-shaped (per blueprint-agents flow),
        so it resolves the blueprint by `iss` and verifies against its JWKS.
        """
        now = int(time.time())
        claims = {
            "iss": self.am_client_id,           # blueprint client_id
            "sub": self.instance_id,            # this running instance
            "aud": self.am_token_url,
            "iat": now,
            "exp": now + ASSERTION_VALIDITY_SECS,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(
            claims,
            self._load_private_key(),
            algorithm="ES256",
            headers={"kid": self.kid},
        )

    def _token_endpoint_data(self, extra: dict) -> dict:
        return {
            **extra,
            "client_id": self.am_client_id,
            "client_assertion_type": "urn:gravitee:params:oauth:client-assertion-type:agent-jwt-bearer",
            "client_assertion": self._build_client_assertion(),
        }

    async def initialize(self):
        logger.info(
            f"AuthService initializing — instance={self.instance_id} kid={self.kid} endpoint={self.am_token_url}"
        )
        await self._refresh_agent_token()
        logger.info("AuthService ready — agent token acquired via jwt-bearer assertion")

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
                data=self._token_endpoint_data({"grant_type": "client_credentials"}),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
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

        subject_token=user token, actor_token=agent token — without actor_token
        AM treats the request as impersonation rather than delegation.
        """
        try:
            exchange_data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            }
            if INCLUDE_ACTOR_TOKEN:
                agent_token = await self.ensure_agent_token()
                exchange_data["actor_token"] = agent_token
                exchange_data["actor_token_type"] = "urn:ietf:params:oauth:token-type:access_token"
            else:
                logger.warning("INCLUDE_ACTOR_TOKEN=false — exchanging without actor_token (impersonation; AM should reject)")
            response = await self._http_client.post(
                self.am_token_url,
                data=self._token_endpoint_data(exchange_data),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
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
