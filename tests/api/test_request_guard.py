"""Tests for safe live-request accounting at the HTTPX transport boundary."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest

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

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, request=request, json={"ok": True})


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
    assert (
        descriptor.path
        == "/api/v9/documents/d/{documentId}/w/{workspaceId}/e/{elementId}/features/{opaque}"
    )
    assert "private" not in str(descriptor)
    assert "canary" not in str(descriptor)


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
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 2, suite)
    inner = FailingTransport()

    async with httpx.AsyncClient(transport=BudgetedAsyncTransport(inner, guard)) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://cad.onshape.com/api/v9/documents/d/private-id")

    assert inner.calls == 1
    assert guard.used == 1
    assert suite.used == 1


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
