"""Tests for safe live-request accounting at the HTTPX transport boundary."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import httpx
import pytest

import onshape_mcp.api.request_guard as request_guard
from onshape_mcp.api.request_guard import (
    BudgetedAsyncTransport,
    LiveApiBudgetExceeded,
    LiveBudgetGuard,
    LiveSuiteBudget,
    sanitize_request,
)


class CountingTransport(httpx.AsyncBaseTransport):
    """Deterministic transport that records physical sends."""

    def __init__(self) -> None:
        self.calls = 0
        self.close_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, request=request, json={"ok": True})

    async def aclose(self) -> None:
        self.close_calls += 1


class RedirectTransport(httpx.AsyncBaseTransport):
    """Returns one redirect then a success response."""

    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls == 1:
            return httpx.Response(
                302,
                headers={"Location": "https://cad.onshape.com/api/v9/documents/d/redirect-target"},
                request=request,
            )
        return httpx.Response(200, request=request)


class FailingTransport(httpx.AsyncBaseTransport):
    """Represents an attempted physical send that could not connect."""

    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise httpx.ConnectError("connection failed", request=request)


def test_sanitize_request_names_onshape_ids_and_discards_query_and_fragment():
    descriptor = sanitize_request(
        "get",
        httpx.URL(
            "https://cad.onshape.com/api/v9/documents/d/private-document-id/"
            "w/private-workspace-id/e/private-element-id/features/private-feature-id"
            "?access_token=query-canary#fragment-canary"
        ),
    )

    assert descriptor.method == "GET"
    assert descriptor.host == "cad.onshape.com"
    assert (
        descriptor.path
        == "/api/v9/documents/d/{documentId}/w/{workspaceId}/e/{elementId}/features/{featureId}"
    )
    assert "private" not in str(descriptor)
    assert "canary" not in str(descriptor)


@pytest.mark.parametrize(
    ("path", "expected_route"),
    [
        (
            "/api/v9/partstudios/d/doc-canary/w/workspace-canary/e/element-canary/"
            "features/v123",
            "/api/v9/partstudios/d/{documentId}/w/{workspaceId}/e/{elementId}/"
            "features/{featureId}",
        ),
        (
            "/api/v9/partstudios/d/doc-canary/w/workspace-canary/e/element-canary/"
            "features/metadata",
            "/api/v9/partstudios/d/{documentId}/w/{workspaceId}/e/{elementId}/"
            "features/{featureId}",
        ),
        (
            "/api/v9/partstudios/d/doc-canary/w/workspace-canary/e/element-canary/"
            "features/featureid/metadata",
            "/api/v9/partstudios/d/{documentId}/w/{workspaceId}/e/{elementId}/"
            "features/featureid/{featureId}",
        ),
        (
            "/api/v9/parts/d/doc-canary/w/workspace-canary/e/element-canary/"
            "partid/v123/massproperties",
            "/api/v9/parts/d/{documentId}/w/{workspaceId}/e/{elementId}/"
            "partid/{partId}/massproperties",
        ),
        (
            "/api/v6/translations/metadata",
            "/api/v6/translations/{translationId}",
        ),
        (
            "/api/v6/documents/d/doc-canary/externaldata/v123",
            "/api/v6/documents/d/{documentId}/externaldata/{externalDataId}",
        ),
    ],
)
def test_sanitize_request_redacts_identifier_slots_regardless_of_static_spelling(
    path, expected_route
):
    descriptor = sanitize_request("GET", httpx.URL(f"https://cad.onshape.com{path}"))

    assert descriptor.path == expected_route
    assert "doc-canary" not in repr(descriptor)
    assert "workspace-canary" not in repr(descriptor)
    assert "element-canary" not in repr(descriptor)


def test_sanitize_request_recognizes_version_syntax_only_in_api_version_position():
    descriptor = sanitize_request(
        "GET",
        httpx.URL("https://cad.onshape.com/api/v123/translations/v456"),
    )

    assert descriptor.path == "/api/v123/translations/{translationId}"


def test_sanitize_request_redacts_unrecognised_path_values():
    descriptor = sanitize_request(
        "POST",
        httpx.URL("https://cad.onshape.com/api/v9/assemblies/private-assembly-id/bom"),
    )

    assert descriptor.method == "POST"
    assert descriptor.path == "/api/v9/assemblies/{opaque}/bom"


def test_read_only_budget_overflow_does_not_consume_a_physical_send():
    suite = LiveSuiteBudget(limit=30)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)

    guard.reserve()

    with pytest.raises(LiveApiBudgetExceeded) as caught:
        guard.reserve()

    assert guard.used == 1
    assert guard.blocked == 1
    assert suite.used == 1
    assert suite.blocked == 1
    assert caught.value.code == "LIVE_API_BUDGET_EXCEEDED"


def test_budget_exception_omits_private_test_identifier_from_text_and_fields():
    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard("private-test-name-canary", 1, suite)
    guard.reserve()

    with pytest.raises(LiveApiBudgetExceeded) as caught:
        guard.reserve()

    assert "private-test-name-canary" not in str(caught.value)
    assert caught.value.test_name is None


def test_successful_reservation_emits_one_immutable_structured_event():
    events = []
    suite = LiveSuiteBudget(limit=30)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 3, suite, event_sink=events)
    descriptor = sanitize_request(
        "get",
        httpx.URL(
            "https://cad.onshape.com/api/v9/documents/d/private-document-id"
            "?token=private-query-canary#private-fragment-canary"
        ),
    )

    guard.reserve(descriptor)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, request_guard.LiveRequestEvent)
    assert event.test_identifier == "LIVE-DEEP-READ-01"
    assert event.method == "GET"
    assert event.host == "cad.onshape.com"
    assert event.route == "/api/v9/documents/d/{documentId}"
    assert event.test_used == 1
    assert event.test_limit == 3
    assert event.suite_used == 1
    assert event.suite_limit == 30
    assert event.blocked_scope == "none"
    with pytest.raises(FrozenInstanceError):
        event.method = "POST"


def test_overflow_events_report_test_suite_and_combined_blocking_scopes():
    test_events = []
    test_suite = LiveSuiteBudget(limit=10)
    test_guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, test_suite, event_sink=test_events)
    test_guard.reserve()
    with pytest.raises(LiveApiBudgetExceeded):
        test_guard.reserve()

    suite_events = []
    suite_budget = LiveSuiteBudget(limit=1)
    LiveBudgetGuard("LIVE-DEEP-READ-01", 3, suite_budget).reserve()
    suite_guard = LiveBudgetGuard(
        "LIVE-DEEP-READ-02", 3, suite_budget, event_sink=suite_events
    )
    with pytest.raises(LiveApiBudgetExceeded):
        suite_guard.reserve()

    both_events = []
    both_suite = LiveSuiteBudget(limit=1)
    both_guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, both_suite, event_sink=both_events)
    both_guard.reserve()
    with pytest.raises(LiveApiBudgetExceeded):
        both_guard.reserve()

    assert test_events[-1].blocked_scope == "test"
    assert len(test_events) == 2
    assert (test_events[-1].test_used, test_events[-1].suite_used) == (1, 1)
    assert suite_events[-1].blocked_scope == "suite"
    assert len(suite_events) == 1
    assert (suite_events[-1].test_used, suite_events[-1].suite_used) == (0, 1)
    assert both_events[-1].blocked_scope == "test_and_suite"
    assert len(both_events) == 2
    assert (both_events[-1].test_used, both_events[-1].suite_used) == (1, 1)


def test_concurrent_reservations_emit_one_atomically_counted_event_per_attempt():
    events = []
    suite = LiveSuiteBudget(limit=5)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 5, suite, event_sink=events)
    descriptor = sanitize_request(
        "GET", httpx.URL("https://cad.onshape.com/api/v9/documents/d/private-id")
    )

    def reserve_once() -> None:
        try:
            guard.reserve(descriptor)
        except LiveApiBudgetExceeded:
            pass

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _: reserve_once(), range(16)))

    assert len(events) == 16
    assert [event.test_used for event in events[:5]] == [1, 2, 3, 4, 5]
    assert [event.blocked_scope for event in events[:5]] == ["none"] * 5
    assert [event.blocked_scope for event in events[5:]] == ["test_and_suite"] * 11
    assert all(event.test_used == 5 and event.suite_used == 5 for event in events[5:])


def test_event_fields_and_repr_contain_no_poison_canaries_or_request_objects():
    events = []
    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard(
        "tests/live/test_private.py::test_case[private-test-name-canary]",
        1,
        suite,
        event_sink=events,
    )
    descriptor = sanitize_request(
        "POST",
        httpx.URL(
            "https://access-key-canary:secret-key-canary@cad.onshape.com/"
            "api/v9/documents/d/private-document-canary/features/metadata"
            "?token=private-query-canary#private-fragment-canary"
        ),
    )

    guard.reserve(descriptor)

    event = events[0]
    event_text = repr(event)
    for poison in (
        "private-test-name-canary",
        "access-key-canary",
        "secret-key-canary",
        "private-document-canary",
        "private-query-canary",
        "private-fragment-canary",
    ):
        assert poison not in event_text
        assert all(poison not in str(value) for value in vars(event).values())
    assert guard.test_name == "LIVE-SCENARIO"
    assert event.test_identifier == "LIVE-SCENARIO"
    assert not any(isinstance(value, (httpx.Request, httpx.Response)) for value in vars(event).values())


def test_unregistered_uppercase_scenario_identifier_is_not_emitted():
    events = []
    suite = LiveSuiteBudget(limit=1)
    private_scenario = "LIVE-PRIVATE-DOCUMENT-CANARY-01"
    guard = LiveBudgetGuard(private_scenario, 1, suite, event_sink=events)

    guard.reserve()

    assert guard.test_name == "LIVE-SCENARIO"
    assert events[0].test_identifier == "LIVE-SCENARIO"
    assert private_scenario not in repr(events[0])


def test_event_sink_rejects_executable_callback_before_accounting():
    suite = LiveSuiteBudget(limit=1)

    with pytest.raises(TypeError, match="event_sink must be a built-in list"):
        LiveBudgetGuard(
            "LIVE-DEEP-READ-01",
            1,
            suite,
            event_sink=lambda event: (_ for _ in ()).throw(RuntimeError("sink ran")),
        )

    assert suite.used == 0


def test_suite_budget_overflow_does_not_consume_a_physical_send():
    suite = LiveSuiteBudget(limit=1)
    first = LiveBudgetGuard("LIVE-DEEP-READ-01", 3, suite)
    second = LiveBudgetGuard("LIVE-DEEP-READ-02", 3, suite)

    first.reserve()

    with pytest.raises(LiveApiBudgetExceeded):
        second.reserve()

    assert first.used == 1
    assert second.used == 0
    assert second.blocked == 1
    assert suite.used == 1
    assert suite.blocked == 1


def test_concurrent_reservations_allow_exactly_the_configured_limit():
    suite = LiveSuiteBudget(limit=5)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 5, suite)

    def reserve_once() -> bool:
        try:
            guard.reserve()
        except LiveApiBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as executor:
        outcomes = list(executor.map(lambda _: reserve_once(), range(16)))

    assert outcomes.count(True) == 5
    assert outcomes.count(False) == 11
    assert guard.used == 5
    assert guard.blocked == 11
    assert suite.used == 5
    assert suite.blocked == 11


@pytest.mark.asyncio
async def test_overflow_is_rejected_without_incrementing_underlying_send_count():
    suite = LiveSuiteBudget(limit=30)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    inner = CountingTransport()
    transport = BudgetedAsyncTransport(inner, guard)

    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        response = await client.get(
            "https://cad.onshape.com/api/v9/documents/d/private-document-id?secret=query-canary"
        )
        assert response.status_code == 200

        with pytest.raises(LiveApiBudgetExceeded) as caught:
            await client.get(
                "https://cad.onshape.com/api/v9/documents/d/private-document-id?secret=query-canary"
            )

    assert inner.calls == 1
    assert caught.value.code == "LIVE_API_BUDGET_EXCEEDED"
    assert "private-document-id" not in str(caught.value)
    assert "query-canary" not in str(caught.value)


@pytest.mark.asyncio
async def test_redirect_reserves_once_per_inner_transport_invocation():
    suite = LiveSuiteBudget(limit=2)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 2, suite)
    inner = RedirectTransport()

    async with httpx.AsyncClient(
        transport=BudgetedAsyncTransport(inner, guard), follow_redirects=True
    ) as client:
        response = await client.get("https://cad.onshape.com/api/v9/documents/d/original-id")

    assert response.status_code == 200
    assert inner.calls == 2
    assert guard.used == 2


@pytest.mark.asyncio
async def test_redirect_overflow_blocks_before_second_inner_invocation():
    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    inner = RedirectTransport()

    async with httpx.AsyncClient(
        transport=BudgetedAsyncTransport(inner, guard), follow_redirects=True
    ) as client:
        with pytest.raises(LiveApiBudgetExceeded):
            await client.get("https://cad.onshape.com/api/v9/documents/d/original-id")

    assert inner.calls == 1
    assert guard.used == 1
    assert guard.blocked == 1


@pytest.mark.asyncio
async def test_failed_inner_transport_consumes_one_reservation():
    suite = LiveSuiteBudget(limit=2)
    events = []
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 2, suite, event_sink=events)
    inner = FailingTransport()

    async with httpx.AsyncClient(transport=BudgetedAsyncTransport(inner, guard)) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://cad.onshape.com/api/v9/documents/d/private-id")

    assert inner.calls == 1
    assert guard.used == 1
    assert suite.used == 1
    assert len(events) == 1
    assert events[0].blocked_scope == "none"
    assert (events[0].test_used, events[0].suite_used) == (1, 1)


@pytest.mark.asyncio
async def test_explicit_retry_consumes_one_reservation_per_attempt():
    suite = LiveSuiteBudget(limit=2)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 2, suite)
    inner = FailingTransport()

    async with httpx.AsyncClient(transport=BudgetedAsyncTransport(inner, guard)) as client:
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await client.get("https://cad.onshape.com/api/v9/documents/d/private-id")

    assert inner.calls == 2
    assert guard.used == 2


@pytest.mark.asyncio
async def test_concurrent_overflow_allows_exactly_n_inner_invocations():
    suite = LiveSuiteBudget(limit=5)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 5, suite)
    inner = CountingTransport()

    async with httpx.AsyncClient(transport=BudgetedAsyncTransport(inner, guard)) as client:
        outcomes = await asyncio.gather(
            *(
                client.get(f"https://cad.onshape.com/api/v9/documents/d/private-id-{index}",)
                for index in range(12)
            ),
            return_exceptions=True,
        )

    assert sum(response.status_code == 200 for response in outcomes if not isinstance(response, Exception)) == 5
    assert sum(isinstance(response, LiveApiBudgetExceeded) for response in outcomes) == 7
    assert inner.calls == 5


@pytest.mark.asyncio
async def test_permit_scopes_only_the_inner_transport_delegate():
    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    inner = CountingTransport()
    events: list[str] = []

    @contextmanager
    def permit():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    transport = BudgetedAsyncTransport(inner, guard, permit)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://cad.onshape.com/api/v9/documents/d/private-id")

    assert events == ["enter", "exit"]


@pytest.mark.asyncio
async def test_aclose_delegates_once_to_the_injected_transport():
    suite = LiveSuiteBudget(limit=1)
    inner = CountingTransport()
    transport = BudgetedAsyncTransport(inner, LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite))

    await transport.aclose()

    assert inner.close_calls == 1
