"""Offline behavior coverage for FeatureScript diagnostic enrichment."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from onshape_mcp.api.fs_notices import (
    extract_fs_body,
    fetch_body_notices,
    format_notice,
    format_notices,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "defineFeature(function(context is Context, id is Id, definition is map) "
            "precondition { annotation {\"Name\": \"Length\"} definition.length is Length; } "
            "{ opExtrude(context, id + \"x\", {\"entities\": qCreatedBy(id)}); })",
            ' opExtrude(context, id + "x", {"entities": qCreatedBy(id)}); ',
        ),
        (
            "defineFeature(function(context is Context) { if (true) { return 1; } })",
            " if (true) { return 1; } ",
        ),
        (
            "defineFeature(function(context is Context, callback is function(x)) "
            "{ return callback(1); })",
            " return callback(1); ",
        ),
        ("export const value = 1;", None),
        ("defineFeature(function(context is Context", None),
        ("defineFeature(function(context is Context) precondition", None),
        (
            "defineFeature(function(context is Context) precondition { definition.x is string; ",
            None,
        ),
        ("defineFeature(function(context is Context) return 1;)", None),
        ("defineFeature(function(context is Context) { return 1;", None),
    ],
)
def test_extract_fs_body_handles_supported_shape_and_malformed_input(source, expected):
    assert extract_fs_body(source) == expected


def test_format_notice_supports_direct_and_structured_messages():
    assert format_notice("not-a-notice") == ""
    assert format_notice(
        {
            "severity": "ERROR",
            "text": "bad expression",
            "stackTrace": [{"line": 12, "column": 4}],
        }
    ) == "[ERROR] L12:C4 bad expression"

    structured = {
        "type": "WARNING",
        "expressionErrorInfo": {
            "errorMessageIdentifier": "CANNOT_RESOLVE",
            "messageArguments": [
                {"value": {"value": "symbol"}},
                {"value": 3},
                {"value": {"other": "ignored"}},
                {"value": None},
                "ignored",
            ],
        },
        "stackTrace": [{"line": 0, "column": 0}],
    }
    assert format_notice(structured) == "[WARNING] CANNOT_RESOLVE: symbol, 3"
    assert format_notice({"level": "INFO"}) == "[INFO]"


def test_format_notices_filters_noise_deduplicates_and_bounds_output():
    notices = [
        {"level": "INFO", "message": "unused variable"},
        {"level": "ERROR", "message": "first"},
        {"level": "ERROR", "message": "first"},
        {"severity": "WARNING", "message": "second"},
        {"level": "ERROR", "message": "third"},
        "ignored",
    ]
    assert format_notices(notices, max_count=2) == "[ERROR] first\n[WARNING] second"
    assert format_notices([]) == ""
    assert format_notices(None) == ""
    assert format_notices([{"level": "INFO", "message": "kept"}]) == "[INFO] kept"
    assert format_notices(["ignored", {"level": "INFO", "message": "kept"}]) == (
        "[INFO] kept"
    )


@pytest.mark.asyncio
async def test_fetch_body_notices_skips_blank_body_and_wraps_valid_body():
    client = SimpleNamespace(post=AsyncMock())
    assert await fetch_body_notices(client, "doc", "ws", "element", "  ") == []
    client.post.assert_not_awaited()

    client.post.return_value = {"notices": [{"level": "ERROR", "message": "boom"}]}
    result = await fetch_body_notices(
        client,
        "doc",
        "ws",
        "element",
        "    opBad(context, id, definition);",
    )
    assert result == [{"level": "ERROR", "message": "boom"}]
    path, = client.post.await_args.args
    assert path == "/api/v8/partstudios/d/doc/w/ws/e/element/featurescript"
    script = client.post.await_args.kwargs["data"]["script"]
    assert "var id = newId();" in script
    assert "var definition = {};" in script
    assert "opBad(context, id, definition);" in script


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"notices": "invalid"}, None])
async def test_fetch_body_notices_rejects_non_list_payloads(response):
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    assert await fetch_body_notices(client, "doc", "ws", "element", "return 1;") == []


@pytest.mark.asyncio
async def test_fetch_body_notices_is_best_effort_on_transport_failure():
    client = SimpleNamespace(post=AsyncMock(side_effect=RuntimeError("synthetic failure")))
    assert await fetch_body_notices(client, "doc", "ws", "element", "return 1;") == []
