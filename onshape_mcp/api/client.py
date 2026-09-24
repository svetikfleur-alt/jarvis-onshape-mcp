"""Onshape API client for REST API communication."""

import base64
import re
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel
from loguru import logger

from .request_guard import sanitize_request


_NAMED_ROUTE_IDENTIFIER = re.compile(
    r"\{(?:documentId|elementId|externalDataId|featureId|microversionId|partId|translationId|workspaceId)\}"
)
_ERROR_CATEGORY = re.compile(r"\b(BT[A-Za-z0-9_]*(?:Exception|Error))\b")
_SAFE_ERROR_CATEGORIES = frozenset({"BTWeirdStringValueException"})
_SAFE_REFERENCE_FIELDS = frozenset(
    {"definition", "entities", "feature", "message", "parameters", "queries"}
)


def _diagnostic_route(method: str, path: str) -> str:
    """Return the common request-guard route with generic identifier labels."""

    normalized_path = path if path.startswith("/") else f"/{path}"
    descriptor = sanitize_request(
        method,
        httpx.URL(f"https://diagnostic.invalid{normalized_path}"),
    )
    return _NAMED_ROUTE_IDENTIFIER.sub("{id}", descriptor.route)


def redact_onshape_route(path: str) -> str:
    """Return a route template without private identifiers or query values."""

    return _diagnostic_route("GET", path)


def _reference_path(text: str) -> Optional[str]:
    marker = "through reference chain:"
    if marker not in text:
        return None
    # Jackson commonly starts the reference chain on the next line. Keep the
    # parser bounded, but allow whitespace/newlines between the marker and the
    # useful path segments.
    chain = text.split(marker, 1)[1][:1024].split(")", 1)[0]
    tokens: list[str] = []
    for key, index in re.findall(r'\["([^"\]]+)"\]|\[(\d+)\]', chain)[:16]:
        if key:
            safe_key = key if key in _SAFE_REFERENCE_FIELDS else "{field}"
            tokens.append(safe_key if not tokens else f".{safe_key}")
        elif index:
            safe_index = index if len(index) <= 4 else "*"
            tokens.append(f"[{safe_index}]")
    return "".join(tokens) or None


def build_http_diagnostic(
    method: str, path: str, response: httpx.Response
) -> Dict[str, Any]:
    """Extract bounded payload-rejection facts without exposing the body."""

    text = response.text[:8192]
    category_match = _ERROR_CATEGORY.search(text)
    category = category_match.group(1) if category_match else None
    return {
        "failure_kind": "http_rejection",
        "method": method.upper(),
        "route": _diagnostic_route(method, path),
        "status_code": response.status_code,
        "category": category if category in _SAFE_ERROR_CATEGORIES else None,
        "reference_path": _reference_path(text),
        "message": "Onshape rejected the request payload.",
    }


class OnshapeHTTPError(httpx.HTTPStatusError):
    """HTTP rejection carrying only bounded, sanitized Onshape diagnostics."""

    def __init__(self, response: httpx.Response, diagnostic: Dict[str, Any]):
        message = (
            f"{diagnostic['method']} {diagnostic['route']} rejected with "
            f"HTTP {diagnostic['status_code']}"
        )
        super().__init__(message, request=response.request, response=response)
        self.onshape_diagnostic = diagnostic


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

    def _sanitize_for_logging(self, data: Any, max_length: int = 200) -> str:
        """Return a bounded structural summary, never arbitrary values.

        Args:
            data: Data to sanitize
            max_length: Maximum length of output string

        Returns:
            Sanitized string safe for logging
        """
        del max_length
        if data is None:
            return "none"
        if isinstance(data, dict):
            # Keys can themselves contain caller/private content. Shape alone
            # is sufficient for routine request/response logging.
            return f"object(count={len(data)})"
        if isinstance(data, list):
            return f"array(count={len(data)})"
        return type(data).__name__

    @staticmethod
    def _raise_for_status(
        response: httpx.Response, *, method: str, path: str
    ) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            if not isinstance(getattr(response, "status_code", None), int):
                # Compatibility with narrowly mocked response objects: preserve
                # their original HTTPStatusError rather than inventing fields.
                raise
        else:
            return
        diagnostic = build_http_diagnostic(method, path, response)
        logger.error(
            "{} {} rejected status={} category={} reference_path={}",
            diagnostic["method"],
            diagnostic["route"],
            diagnostic["status_code"],
            diagnostic["category"],
            diagnostic["reference_path"],
        )
        raise OnshapeHTTPError(response, diagnostic) from None

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
        route = redact_onshape_route(path)
        logger.debug("GET {} params={}", route, self._sanitize_for_logging(params))
        response = await self._client.get(url, params=params, headers=headers)
        self._raise_for_status(response, method="GET", path=path)
        result = response.json()
        logger.debug("GET {} response={}", route, self._sanitize_for_logging(result))
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
        route = redact_onshape_route(path)
        logger.debug("GET(raw) {} params={}", route, self._sanitize_for_logging(params))
        response = await self._client.get(
            url, params=params, headers=headers, follow_redirects=follow_redirects
        )
        self._raise_for_status(response, method="GET", path=path)
        logger.debug("GET(raw) {} returned {} bytes", route, len(response.content))
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
        route = redact_onshape_route(path)
        logger.debug("POST {} params={}", route, self._sanitize_for_logging(params))
        logger.debug("POST {} body={}", route, self._sanitize_for_logging(data))
        response = await self._client.post(url, json=data, params=params, headers=headers)
        self._raise_for_status(response, method="POST", path=path)
        if not response.content:
            logger.debug("POST {} returned empty body status={}", route, response.status_code)
            return {}
        result = response.json()
        logger.debug("POST {} response={}", route, self._sanitize_for_logging(result))
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
        self._raise_for_status(response, method="DELETE", path=path)
        if not response.content:
            return {}
        return response.json()

    async def close(self):
        """Close the HTTP client and clean up resources."""
        if self._client and self._own_client:
            await self._client.aclose()
            self._client = None
