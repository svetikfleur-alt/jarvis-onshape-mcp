"""Safe accounting for live HTTP requests at the supported HTTPX boundary."""

from contextlib import nullcontext
from dataclasses import dataclass
from threading import Lock
from typing import Callable, ContextManager, Optional

import httpx


_SAFE_PATH_SEGMENTS = frozenset(
    {
        "api",
        "assemblies",
        "bom",
        "configuration",
        "current",
        "documents",
        "elements",
        "export",
        "externaldata",
        "features",
        "metadata",
        "partstudios",
        "tabs",
        "thumbnails",
        "translations",
        "variables",
        "versions",
        "workspaces",
    }
)
_NAMED_IDENTIFIERS = {
    "d": "documentId",
    "e": "elementId",
    "m": "microversionId",
    "w": "workspaceId",
}


@dataclass(frozen=True)
class RequestDescriptor:
    """The non-sensitive request shape allowed in diagnostics."""

    method: str
    path: str


def sanitize_request(method: str, url: httpx.URL) -> RequestDescriptor:
    """Return a diagnostic-safe request shape with no query, fragment, or IDs."""
    parts = [part for part in url.path.split("/") if part]
    sanitized: list[str] = []
    identifier_name: Optional[str] = None

    for index, part in enumerate(parts):
        if identifier_name is not None:
            sanitized.append(f"{{{identifier_name}}}")
            identifier_name = None
        elif part in _NAMED_IDENTIFIERS:
            sanitized.append(part)
            identifier_name = _NAMED_IDENTIFIERS[part]
        elif part == "documents" and (index + 1 == len(parts) or parts[index + 1] != "d"):
            sanitized.append(part)
            identifier_name = "documentId"
        elif part in _SAFE_PATH_SEGMENTS or (part.startswith("v") and part[1:].isdigit()):
            sanitized.append(part)
        else:
            sanitized.append("{opaque}")

    if identifier_name is not None:
        sanitized.append(f"{{{identifier_name}}}")

    return RequestDescriptor(method=method.upper(), path="/" + "/".join(sanitized))


class LiveApiBudgetExceeded(RuntimeError):
    """Raised before an outbound send would exceed a configured live budget."""

    code = "LIVE_API_BUDGET_EXCEEDED"

    def __init__(
        self,
        descriptor: RequestDescriptor,
        test_name: str,
        test_used: int,
        test_limit: int,
        suite_used: int,
        suite_limit: int,
    ) -> None:
        self.descriptor = descriptor
        self.test_name = test_name
        self.test_used = test_used
        self.test_limit = test_limit
        self.suite_used = suite_used
        self.suite_limit = suite_limit
        super().__init__(
            f"{self.code}: request={descriptor.method} {descriptor.path}; "
            f"test={test_name} used={test_used}/{test_limit}; "
            f"suite used={suite_used}/{suite_limit}"
        )


class LiveSuiteBudget:
    """Thread-safe physical-send budget shared by all selected live tests."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("Live suite budget limit must be non-negative")
        self.limit = limit
        self.used = 0
        self.blocked = 0
        self._lock = Lock()


class LiveBudgetGuard:
    """Per-test budget coordinated atomically with a shared suite budget."""

    def __init__(self, test_name: str, test_limit: int, suite_budget: LiveSuiteBudget) -> None:
        if test_limit < 0:
            raise ValueError("Live test budget limit must be non-negative")
        self.test_name = test_name
        self.test_limit = test_limit
        self.suite_budget = suite_budget
        self.used = 0
        self.blocked = 0

    def reserve(self, descriptor: Optional[RequestDescriptor] = None) -> None:
        """Atomically reserve one physical send or fail before it reaches HTTPX."""
        request = descriptor or RequestDescriptor(method="UNKNOWN", path="/{opaque}")
        suite = self.suite_budget
        with suite._lock:
            if self.used >= self.test_limit or suite.used >= suite.limit:
                self.blocked += 1
                suite.blocked += 1
                raise LiveApiBudgetExceeded(
                    request,
                    self.test_name,
                    self.used,
                    self.test_limit,
                    suite.used,
                    suite.limit,
                )
            self.used += 1
            suite.used += 1


class BudgetedAsyncTransport(httpx.AsyncBaseTransport):
    """Supported HTTPX transport wrapper that accounts for every inner send."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        guard: LiveBudgetGuard,
        permit_factory: Callable[[], ContextManager[None]] = nullcontext,
    ) -> None:
        self._inner = inner
        self._guard = guard
        self._permit_factory = permit_factory

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._guard.reserve(sanitize_request(request.method, request.url))
        with self._permit_factory():
            return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
