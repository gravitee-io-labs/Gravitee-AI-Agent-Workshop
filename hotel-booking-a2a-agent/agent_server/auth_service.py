"""Authentication service for handling JWT and OIDC operations."""
import logging
from typing import Dict, Any, Optional
import jwt
import httpx
from datetime import datetime, timedelta

from agent_server.colored_logger import get_agent_logger

logger = get_agent_logger(__name__)


class AuthenticationError(Exception):
    """Custom exception for authentication errors."""
    pass


class AuthService:
    """Service for handling OAuth2/OIDC authentication and JWT operations."""
    
    def __init__(self, oidc_discovery_url: str, jwt_secret: str):
        """
        Initialize the AuthService.
        
        Args:
            oidc_discovery_url: URL to the OIDC discovery endpoint (.well-known/openid-configuration)
            jwt_secret: Secret key for signing JWT tokens
        """
        self.oidc_discovery_url = oidc_discovery_url
        self.jwt_secret = jwt_secret
        self.userinfo_endpoint: Optional[str] = None
        self._http_client = httpx.AsyncClient(timeout=30.0)
    
    async def initialize(self):
        """Initialize the service by discovering OIDC endpoints."""
        try:
            logger.debug(f"Discovering OIDC configuration from {self.oidc_discovery_url}")
            response = await self._http_client.get(self.oidc_discovery_url)
            response.raise_for_status()
            
            oidc_config = response.json()
            self.userinfo_endpoint = oidc_config.get("userinfo_endpoint")
            
            if not self.userinfo_endpoint:
                raise AuthenticationError("userinfo_endpoint not found in OIDC discovery configuration")
            
            logger.debug(f"Successfully discovered userinfo endpoint: {self.userinfo_endpoint}")
            
        except Exception as e:
            logger.error(f"Failed to discover OIDC configuration: {e}")
            raise AuthenticationError(f"Failed to discover OIDC configuration: {e}")
    
    async def get_user_email_from_token(self, access_token: str) -> str:
        """
        Get user email from access token by calling the userinfo endpoint.
        
        Args:
            access_token: The access token (Bearer token)
        
        Returns:
            The user's email address
            
        Raises:
            AuthenticationError: If the token is invalid or email cannot be retrieved
        """
        if not self.userinfo_endpoint:
            raise AuthenticationError("AuthService not initialized. Call initialize() first.")
        
        try:
            logger.debug("Calling userinfo endpoint to retrieve user email")
            
            # Call userinfo endpoint with the access token
            headers = {
                "Authorization": f"Bearer {access_token}"
            }
            
            response = await self._http_client.get(self.userinfo_endpoint, headers=headers)
            response.raise_for_status()
            
            userinfo = response.json()
            email = userinfo.get("email")
            
            if not email:
                raise AuthenticationError("Email not found in userinfo response")
            
            logger.debug(f"Successfully retrieved user email: {email}")
            return email
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Invalid or expired access token")
            logger.error(f"HTTP error calling userinfo endpoint: {e}")
            raise AuthenticationError(f"Failed to retrieve user info: {e}")
        except Exception as e:
            logger.error(f"Error retrieving user email: {e}")
            raise AuthenticationError(f"Failed to retrieve user email: {e}")
    
    def create_internal_jwt(self, email: str, expiration_seconds: int = 60) -> str:
        """
        Create an internal JWT token with user email.
        
        Args:
            email: The user's email to include in the token
            expiration_seconds: Token expiration time in seconds (default: 60)
        
        Returns:
            The signed JWT token
        """
        try:
            now = datetime.utcnow()
            expiration = now + timedelta(seconds=expiration_seconds)
            
            payload = {
                "sub-email": email,
                "iat": int(now.timestamp()),
                "exp": int(expiration.timestamp())
            }
            
            token = jwt.encode(payload, self.jwt_secret, algorithm="HS256")
            logger.debug(f"Created internal JWT token for email: {email} (expires in {expiration_seconds}s)")
            
            return token
            
        except Exception as e:
            logger.error(f"Error creating internal JWT: {e}")
            raise AuthenticationError(f"Failed to create internal JWT: {e}")
    
    async def process_authorization_for_tool(self, authorization_header: Optional[str]) -> str:
        """
        Process authorization header and create internal JWT for tool call.
        
        Args:
            authorization_header: The Authorization header value (e.g., "Bearer <token>")
        
        Returns:
            The internal JWT token to use for tool call
            
        Raises:
            AuthenticationError: If authorization fails (401)
        """
        if not authorization_header:
            raise AuthenticationError("No Authorization header provided")
        
        # Extract the token from "Bearer <token>" format
        if not authorization_header.startswith("Bearer "):
            raise AuthenticationError("Invalid Authorization header format. Expected 'Bearer <token>'")
        
        access_token = authorization_header.replace("Bearer ", "", 1).strip()
        
        if not access_token:
            raise AuthenticationError("Empty access token")
        
        # Get user email from the access token
        email = await self.get_user_email_from_token(access_token)
        
        # Create internal JWT with the email
        internal_jwt = self.create_internal_jwt(email)
        
        return internal_jwt
    
    async def cleanup(self):
        """Clean up resources."""
        try:
            await self._http_client.aclose()
            logger.info("AuthService cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during AuthService cleanup: {e}")
