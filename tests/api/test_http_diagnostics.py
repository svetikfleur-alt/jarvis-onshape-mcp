"""Privacy regression tests for HTTP failure diagnostics."""

from __future__ import annotations

import json
import base64
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from loguru import logger

from onshape_mcp.api import request_guard
from onshape_mcp.api.rendering import ShadedViewManager
from onshape_mcp.server import _exception_json, call_tool


PRIVATE_DOCUMENT_ID = "private-document-canary"
PRIVATE_WORKSPACE_ID = "private-workspace-canary"
PRIVATE_ELEMENT_ID = "private-element-canary"
PRIVATE_QUERY = "private-query-canary"
PRIVATE_BODY = "private-response-body-canary"


def _status_error() -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET",
        f"https://cad.onshape.com/api/v9/documents/d/{PRIVATE_DOCUMENT_ID}"
        f"?token={PRIVATE_QUERY}",
    )
    response = httpx.Response(
        403,
        request=request,
        headers={"Content-Length": str(len(PRIVATE_BODY))},
        text=PRIVATE_BODY,
    )
    return httpx.HTTPStatusError(
        f"failure for {request.url}: {PRIVATE_BODY}",
        request=request,
        response=response,
    )


def _request_error() -> httpx.ConnectError:
    request = httpx.Request(
        "GET",
        f"https://cad.onshape.com/api/v9/documents/d/{PRIVATE_DOCUMENT_ID}"
        f"?token={PRIVATE_QUERY}",
    )
    return httpx.ConnectError(
        f"connection failed for {request.url}",
        request=request,
    )


def test_safe_http_diagnostic_ignores_untrusted_response_reason_phrase():
    """A server-controlled custom reason phrase must not enter diagnostics."""
    error = _status_error()
    error.response.extensions["reason_phrase"] = PRIVATE_DOCUMENT_ID.encode()

    diagnostic = request_guard.safe_http_diagnostic(error)

    assert diagnostic.reason_phrase == "Forbidden"
    _assert_no_poison(diagnostic)


def _assert_no_poison(value: object) -> None:
    diagnostic = str(value)
    assert PRIVATE_DOCUMENT_ID not in diagnostic
    assert PRIVATE_WORKSPACE_ID not in diagnostic
    assert PRIVATE_ELEMENT_ID not in diagnostic
    assert PRIVATE_QUERY not in diagnostic
    assert PRIVATE_BODY not in diagnostic


def test_safe_http_diagnostic_exposes_only_bounded_metadata():
    """A raw HTTPX object or string escaping the helper must fail this test."""
    error = _status_error()

    diagnostic = request_guard.safe_http_diagnostic(error)

    assert diagnostic.error_class == "HTTPStatusError"
    assert diagnostic.status_code == 403
    assert diagnostic.method == "GET"
    assert diagnostic.host == "cad.onshape.com"
    assert diagnostic.route == "/api/v9/documents/d/{documentId}"
    assert diagnostic.content_length == len(PRIVATE_BODY)
    assert len(diagnostic.reason_phrase or "") <= 80
    _assert_no_poison(diagnostic)
    # Sanitization must not destroy structured data Jarvis may inspect.
    assert error.request.url.params["token"] == PRIVATE_QUERY
    assert error.response.text == PRIVATE_BODY


def test_exception_json_sanitizes_request_error_without_losing_safe_context():
    """Stringifying RequestError in tool JSON must not disclose its raw URL."""
    payload = json.loads(_exception_json(_request_error(), tool_name="test_tool"))

    assert payload["status"] == "EXCEPTION"
    assert payload["tool"] == "test_tool"
    assert "ConnectError" in payload["error_message"]
    assert "GET" in payload["error_message"]
    assert "cad.onshape.com" in payload["error_message"]
    assert "/api/v9/documents/d/{documentId}" in payload["error_message"]
    _assert_no_poison(payload)


@pytest.mark.asyncio
async def test_rendering_debug_log_uses_sanitized_route_without_params():
    """Render debug diagnostics must not emit CAD IDs, matrices, or raw params."""
    events: list[str] = []
    sink_id = logger.add(events.append, format="{message}", level="DEBUG")
    client = Mock()
    client.get = AsyncMock(
        return_value={"images": [base64.b64encode(b"png-canary").decode("ascii")]}
    )

    try:
        manager = ShadedViewManager(client)
        await manager.render_part_studio_views(
            PRIVATE_DOCUMENT_ID,
            PRIVATE_WORKSPACE_ID,
            PRIVATE_ELEMENT_ID,
            views=[PRIVATE_QUERY],
        )
    finally:
        logger.remove(sink_id)

    log_output = "".join(events)
    assert "GET" in log_output
    assert (
        "/api/v9/partstudios/d/{documentId}/w/{workspaceId}/e/{elementId}/shadedviews"
        in log_output
    )
    _assert_no_poison(log_output)


@pytest.mark.asyncio
async def test_start_model_context_request_error_log_omits_raw_exception_url():
    """Transport failures must not leak through a generic exception traceback."""
    events: list[str] = []
    sink_id = logger.add(events.append, format="{message}")
    record = Mock()
    record.working_state.context_handle = "safe-context-handle"
    record.working_state.budget.record_call = Mock()

    try:
        with (
            patch("onshape_mcp.server.context_store") as context_store,
            patch("onshape_mcp.server.partstudio_manager") as manager,
        ):
            context_store.create.return_value = record
            manager.get_features = AsyncMock(side_effect=_request_error())
            result = await call_tool(
                "start_model_context",
                {
                    "documentId": PRIVATE_DOCUMENT_ID,
                    "workspaceId": PRIVATE_WORKSPACE_ID,
                    "elementId": PRIVATE_ELEMENT_ID,
                },
            )
    finally:
        logger.remove(sink_id)

    assert json.loads(result[0].text)["error"]["type"] == "context_start_failed"
    log_output = "".join(events)
    assert "ConnectError" in log_output
    assert "/api/v9/documents/d/{documentId}" in log_output
    _assert_no_poison(log_output)


@pytest.mark.asyncio
async def test_render_views_request_error_output_omits_raw_exception_url():
    """Rendering transport failures must return a safe descriptor."""
    with patch("onshape_mcp.server.shaded_view_manager") as manager:
        manager.render_part_studio_views = AsyncMock(side_effect=_request_error())
        result = await call_tool(
            "render_part_studio_views",
            {
                "documentId": PRIVATE_DOCUMENT_ID,
                "workspaceId": PRIVATE_WORKSPACE_ID,
                "elementId": PRIVATE_ELEMENT_ID,
            },
        )

    assert "ConnectError" in result[0].text
    assert "/api/v9/documents/d/{documentId}" in result[0].text
    _assert_no_poison(result[0].text)


def test_server_has_no_raw_http_response_body_diagnostic_access():
    """Reintroducing direct response-text diagnostics must fail review automation."""
    server_source = (
        Path(__file__).parents[2] / "onshape_mcp" / "server.py"
    ).read_text(encoding="utf-8")

    assert ".response.text" not in server_source
