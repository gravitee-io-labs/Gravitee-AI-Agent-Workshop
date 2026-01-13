"""Authentication service for handling JWT and OIDC operations."""
import base64
import logging
from typing import Dict, Any, Optional, Tuple
import httpx

from agent_server.colored_logger import get_agent_logger

logger = get_agent_logger(__name__)


class AuthenticationError(Exception):
    """Custom exception for authentication errors."""
    pass


class AuthService:
    """Service for handling OAuth2/OIDC authentication and JWT operations."""
    
    def __init__(self, oidc_discovery_url: str, am_token_url: str, am_client_id: str, am_client_secret: str, ):
        """
        Initialize the AuthService.
        
        Args:
            oidc_discovery_url: URL to the OIDC discovery endpoint (.well-known/openid-configuration)
            am_token_url: URL to the Gravitee AM token endpoint
            am_client_id: Client ID for Gravitee AM
            am_client_secret: Client secret for Gravitee AM
        """
        self.oidc_discovery_url = oidc_discovery_url
        self.am_token_url = am_token_url
        self.am_client_id = am_client_id
        self.am_client_secret = am_client_secret
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
    
    async def get_am_access_token(self, email: str) -> str:
        """
        Get an access token from Gravitee Access Management using client credentials.
        
        Returns:
            The access token from Gravitee AM
            
        Raises:
            AuthenticationError: If token retrieval fails
        """
        try:
            logger.debug(f"Requesting access token from Gravitee AM: {self.am_token_url}")
            
            # OAuth2 client credentials grant with Basic Auth
            credentials = f"{self.am_client_id}:{self.am_client_secret}"
            basic_auth = base64.b64encode(credentials.encode()).decode()
            
            data = {
                "grant_type": "client_credentials"
            }
            
            headers = {
                "sub-email": email,
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic_auth}"
            }
            
            response = await self._http_client.post(self.am_token_url, data=data, headers=headers)
            response.raise_for_status()
            
            token_response = response.json()
            access_token = token_response.get("access_token")
            
            if not access_token:
                raise AuthenticationError("No access_token in AM response")
            
            logger.debug("Successfully obtained access token from Gravitee AM")
            return access_token
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting AM token: {e.response.status_code} - {e.response.text}")
            raise AuthenticationError(f"Failed to get AM token: {e}")
        except Exception as e:
            logger.error(f"Error getting AM access token: {e}")
            raise AuthenticationError(f"Failed to get AM access token: {e}")
    
    async def process_authorization_for_tool(self, authorization_header: Optional[str]) -> Tuple[str, str]:
        """
        Process authorization header and get AM token for tool call.
        
        Args:
            authorization_header: The Authorization header value (e.g., "Bearer <token>")
        
        Returns:
            A tuple of (access_token, user_email) to use for tool call
            
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
        
        # Get access token from Gravitee AM
        am_token = await self.get_am_access_token(email)
        
        return am_token, email
    
    async def cleanup(self):
        """Clean up resources."""
        try:
            await self._http_client.aclose()
            logger.info("AuthService cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during AuthService cleanup: {e}")
