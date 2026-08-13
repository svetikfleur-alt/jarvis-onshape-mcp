"""Onshape API client for REST API communication."""

import base64
import httpx
from typing import Any, Dict, Optional
from pydantic import BaseModel
from loguru import logger


class OnshapeCredentials(BaseModel):
    """Onshape API credentials."""

    access_key: str
    secret_key: str
    base_url: str = "https://cad.onshape.com"


class OnshapeClient:
    """Client for interacting with Onshape REST API.

    Use as an async context manager to ensure proper cleanup:
        async with OnshapeClient(credentials) as client:
            result = await client.get("/api/v9/documents")
    """

    def __init__(
        self, credentials: OnshapeCredentials, *, transport: Optional[httpx.AsyncBaseTransport] = None
    ):
        """Initialize the Onshape client.

        Args:
            credentials: Onshape API credentials (access key and secret key)
        """
        self.credentials = credentials
        self.base_url = credentials.base_url
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._own_client = False

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=30.0, transport=self._transport)
        self._own_client = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures cleanup."""
        await self.close()
        return False

    def _ensure_client(self):
        """Ensure HTTP client is initialized."""
        if self._client is None:
            # Create client if not using context manager (backwards compatibility)
            self._client = httpx.AsyncClient(timeout=30.0, transport=self._transport)
            self._own_client = True

    def _get_auth_header(self) -> str:
        """Generate Basic Auth header from credentials.

        Returns:
            Authorization header value
        """
        auth_string = f"{self.credentials.access_key}:{self.credentials.secret_key}"
        encoded = base64.b64encode(auth_string.encode()).decode()
        return f"Basic {encoded}"

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a GET request to Onshape API.

        Args:
            path: API endpoint path (e.g., "/api/v9/documents")
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }

        self._ensure_client()
        response = await self._client.get(url, params=params, headers=headers)
        response.raise_for_status()
        result = response.json()
        logger.debug("Onshape GET request completed with status {}", response.status_code)
        return result

    async def get_raw(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        follow_redirects: bool = True,
    ) -> bytes:
        """Make a GET request that returns the raw response body.

        Used for binary payloads such as translation downloads and thumbnails
        where the response is not JSON.

        Args:
            path: API endpoint path
            params: Query parameters
            follow_redirects: Whether to follow 302s (Onshape often redirects
                external-data downloads to a signed URL)

        Returns:
            Raw response bytes
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
        }

        self._ensure_client()
        response = await self._client.get(
            url, params=params, headers=headers, follow_redirects=follow_redirects
        )
        response.raise_for_status()
        logger.debug("Onshape raw GET request completed with status {}", response.status_code)
        return response.content

    async def post(
        self,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a POST request to Onshape API.

        Args:
            path: API endpoint path
            data: JSON body data
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
            "Content-Type": "application/json;charset=UTF-8; qs=0.09",
        }

        self._ensure_client()
        response = await self._client.post(url, json=data, params=params, headers=headers)

        # Log error details if request failed
        if response.status_code >= 400:
            logger.error("Onshape POST request failed with status {}", response.status_code)

        response.raise_for_status()
        if not response.content:
            logger.debug("Onshape POST request completed with status {}", response.status_code)
            return {}
        result = response.json()
        logger.debug("Onshape POST request completed with status {}", response.status_code)
        return result

    async def delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a DELETE request to Onshape API.

        Args:
            path: API endpoint path
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self._get_auth_header(),
            "Accept": "application/json;charset=UTF-8; qs=0.09",
        }

        self._ensure_client()
        response = await self._client.delete(url, params=params, headers=headers)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def close(self):
        """Close the HTTP client and clean up resources."""
        if self._client and self._own_client:
            await self._client.aclose()
            self._client = None
