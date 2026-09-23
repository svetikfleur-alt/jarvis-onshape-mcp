"""Safe accounting for live HTTP requests at the supported HTTPX boundary."""

import re
from http import HTTPStatus
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from threading import Lock
from typing import ContextManager, Literal, Optional

import httpx


_ROOT_RESOURCES = frozenset(
    {
        "assemblies",
        "documents",
        "externaldata",
        "features",
        "parts",
        "partstudios",
        "translations",
    }
)
_STATIC_ROUTE_SEGMENTS = frozenset(
    {
        "bodydetails",
        "bom",
        "configuration",
        "current",
        "elements",
        "export",
        "featurescript",
        "massproperties",
        "metadata",
        "shadedviews",
        "tabs",
        "thumbnails",
        "variables",
        "versions",
        "workspaces",
    }
)
_IDENTIFIER_MARKERS = {
    "d": "documentId",
    "e": "elementId",
    "featureid": "featureId",
    "m": "microversionId",
    "partid": "partId",
    "w": "workspaceId",
}
_COLLECTION_IDENTIFIERS = {
    "documents": "documentId",
    "externaldata": "externalDataId",
    "features": "featureId",
    "parts": "partId",
    "translations": "translationId",
}
_SAFE_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_SAFE_SCENARIOS = frozenset({"LIVE-DEEP-READ-01"})
_GENERIC_SCENARIO = "LIVE-SCENARIO"


def _identifier_after_collection(
    collection: str, next_segment: Optional[str]
) -> Optional[str]:
    """Name an identifier only when the route position is unambiguous."""
    if next_segment is None:
        return None
    if collection in {"documents", "parts"} and next_segment in _IDENTIFIER_MARKERS:
        return None
    if collection == "features" and next_segment == "featureid":
        return None
    return _COLLECTION_IDENTIFIERS[collection]


def _sanitize_test_identifier(test_name: str) -> str:
    """Keep only explicitly registered, non-private live scenario identifiers."""
    if test_name in _SAFE_SCENARIOS:
        return test_name
    return _GENERIC_SCENARIO


@dataclass(frozen=True)
class RequestDescriptor:
    """The non-sensitive request shape allowed in diagnostics."""

    method: str
    path: str
    host: str = "{unknown}"

    @property
    def route(self) -> str:
        """Alias the sanitized path using telemetry terminology."""
        return self.path


@dataclass(frozen=True)
class SafeHttpDiagnostic:
    """Bounded HTTP failure metadata safe for logs and tool output."""

    error_class: str
    status_code: Optional[int]
    reason_phrase: Optional[str]
    content_length: Optional[int]
    method: str
    host: str
    route: str

    def __str__(self) -> str:
        fields = [
            f"error={self.error_class}",
            f"request={self.method} {self.host}{self.route}",
        ]
        if self.status_code is not None:
            fields.append(f"status={self.status_code}")
        if self.reason_phrase:
            fields.append(f"reason={self.reason_phrase}")
        if self.content_length is not None:
            fields.append(f"content_length={self.content_length}")
        return "; ".join(fields)


BlockedScope = Literal["none", "test", "suite", "test_and_suite"]


@dataclass(frozen=True)
class LiveRequestEvent:
    """Immutable diagnostic-safe telemetry for one reservation attempt."""

    test_identifier: str
    method: str
    host: str
    route: str
    test_used: int
    test_limit: int
    suite_used: int
    suite_limit: int
    blocked_scope: BlockedScope


EventSink = list[LiveRequestEvent]


def sanitize_request(method: str, url: httpx.URL) -> RequestDescriptor:
    """Return a diagnostic-safe request shape with no query, fragment, or IDs."""
    parts = [part for part in url.path.split("/") if part]
    sanitized: list[str] = []
    identifier_name: Optional[str] = None
    known_route = False

    for index, part in enumerate(parts):
        if identifier_name is not None:
            sanitized.append(f"{{{identifier_name}}}")
            identifier_name = None
        elif index == 0:
            sanitized.append("api" if part == "api" else "{opaque}")
        elif index == 1:
            is_version = parts[0] == "api" and re.fullmatch(r"v\d+", part) is not None
            sanitized.append(part if is_version else "{opaque}")
        elif index == 2:
            if parts[0] == "api" and part in _ROOT_RESOURCES:
                sanitized.append(part)
                known_route = True
                if part in _COLLECTION_IDENTIFIERS:
                    identifier_name = _identifier_after_collection(
                        part, parts[index + 1] if index + 1 < len(parts) else None
                    )
            else:
                sanitized.append("{opaque}")
        elif not known_route:
            sanitized.append("{opaque}")
        elif part in _IDENTIFIER_MARKERS:
            sanitized.append(part)
            identifier_name = _IDENTIFIER_MARKERS[part]
        elif part in _COLLECTION_IDENTIFIERS:
            sanitized.append(part)
            identifier_name = _identifier_after_collection(
                part, parts[index + 1] if index + 1 < len(parts) else None
            )
        elif part in _STATIC_ROUTE_SEGMENTS:
            sanitized.append(part)
        else:
            sanitized.append("{opaque}")

    if identifier_name is not None:
        sanitized.append(f"{{{identifier_name}}}")

    normalized_method = method.upper()
    if normalized_method not in _SAFE_METHODS:
        normalized_method = "UNKNOWN"
    return RequestDescriptor(
        method=normalized_method,
        host=url.host or "{unknown}",
        path="/" + "/".join(sanitized),
    )


def _standard_reason_phrase(status_code: int) -> Optional[str]:
    """Return the standard library's phrase, never server-controlled text."""
    try:
        return HTTPStatus(status_code).phrase[:80]
    except ValueError:
        return None


def _declared_content_length(response: object) -> Optional[int]:
    """Read only a bounded numeric Content-Length header, never the body."""
    if not isinstance(response, httpx.Response):
        return None
    value = response.headers.get("Content-Length")
    if value is None or re.fullmatch(r"\d{1,20}", value) is None:
        return None
    return int(value)


def safe_http_diagnostic(error: httpx.HTTPError) -> SafeHttpDiagnostic:
    """Describe an HTTPX failure without retaining raw URLs or payloads."""
    request: object = None
    try:
        request = error.request
    except RuntimeError:
        pass

    descriptor = (
        sanitize_request(request.method, request.url)
        if isinstance(request, httpx.Request)
        else RequestDescriptor(method="UNKNOWN", path="/{opaque}")
    )
    response = error.response if isinstance(error, httpx.HTTPStatusError) else None
    status_code = None
    reason_phrase = None
    if isinstance(response, httpx.Response):
        status_code = response.status_code
        reason_phrase = _standard_reason_phrase(status_code)

    return SafeHttpDiagnostic(
        error_class=type(error).__name__,
        status_code=status_code,
        reason_phrase=reason_phrase,
        content_length=_declared_content_length(response),
        method=descriptor.method,
        host=descriptor.host,
        route=descriptor.route,
    )


def safe_exception_message(error: BaseException) -> str:
    """Preserve ordinary diagnostics while redacting HTTPX request details."""
    if isinstance(error, httpx.HTTPError):
        return str(safe_http_diagnostic(error))
    return str(error)


class LiveApiBudgetExceeded(RuntimeError):
    """Raised before an outbound send would exceed a configured live budget."""

    code = "LIVE_API_BUDGET_EXCEEDED"

    def __init__(
        self,
        descriptor: RequestDescriptor,
        test_used: int,
        test_limit: int,
        suite_used: int,
        suite_limit: int,
    ) -> None:
        self.descriptor = descriptor
        self.test_name = None
        self.test_used = test_used
        self.test_limit = test_limit
        self.suite_used = suite_used
        self.suite_limit = suite_limit
        super().__init__(
            f"{self.code}: request={descriptor.method} {descriptor.path}; "
            f"test used={test_used}/{test_limit}; "
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

    def __init__(
        self,
        test_name: str,
        test_limit: int,
        suite_budget: LiveSuiteBudget,
        event_sink: Optional[EventSink] = None,
    ) -> None:
        if test_limit < 0:
            raise ValueError("Live test budget limit must be non-negative")
        if event_sink is not None and type(event_sink) is not list:
            raise TypeError("event_sink must be a built-in list")
        self.test_name = _sanitize_test_identifier(test_name)
        self.test_limit = test_limit
        self.suite_budget = suite_budget
        self._event_sink = event_sink
        self.used = 0
        self.blocked = 0

    def _emit_event(
        self, descriptor: RequestDescriptor, blocked_scope: BlockedScope
    ) -> None:
        sink = self._event_sink
        if sink is None:
            return
        event = LiveRequestEvent(
            test_identifier=self.test_name,
            method=descriptor.method,
            host=descriptor.host,
            route=descriptor.route,
            test_used=self.used,
            test_limit=self.test_limit,
            suite_used=self.suite_budget.used,
            suite_limit=self.suite_budget.limit,
            blocked_scope=blocked_scope,
        )
        sink.append(event)

    def reserve(self, descriptor: Optional[RequestDescriptor] = None) -> None:
        """Atomically reserve one physical send or fail before it reaches HTTPX."""
        request = descriptor or RequestDescriptor(method="UNKNOWN", path="/{opaque}")
        suite = self.suite_budget
        with suite._lock:
            test_blocked = self.used >= self.test_limit
            suite_blocked = suite.used >= suite.limit
            if test_blocked or suite_blocked:
                self.blocked += 1
                suite.blocked += 1
                if test_blocked and suite_blocked:
                    blocked_scope: BlockedScope = "test_and_suite"
                elif test_blocked:
                    blocked_scope = "test"
                else:
                    blocked_scope = "suite"
                self._emit_event(request, blocked_scope)
                raise LiveApiBudgetExceeded(
                    request,
                    self.used,
                    self.test_limit,
                    suite.used,
                    suite.limit,
                )
            self.used += 1
            suite.used += 1
            self._emit_event(request, "none")


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
