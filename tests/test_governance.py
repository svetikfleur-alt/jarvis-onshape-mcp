"""Focused tests for the token-governance pilot tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from onshape_mcp import server
from onshape_mcp.governance import BudgetPolicy, ContextStore
from onshape_mcp.server import call_tool, list_tools


def _feature_payload(count: int = 3, *, name_size: int = 0) -> dict:
    features = []
    states = {}
    for index in range(count):
        feature_id = f"feature-{index}"
        features.append(
            {
                "btType": "BTMSketch-151",
                "featureId": feature_id,
                "featureType": "newSketch",
                "name": f"Feature {index}" + ("x" * name_size),
                "suppressed": index == 1,
                "parameters": [{"secret": "raw-payload-marker"}],
            }
        )
        states[feature_id] = {
            "featureStatus": ("ERROR" if index == 2 else "OK"),
            "messages": ([{"severity": "ERROR", "message": "bad"}] if index == 2 else []),
        }
    return {"features": features, "featureStates": states, "serializationVersion": "1.2.3"}


def _decode(result) -> dict:
    assert len(result) == 1
    return json.loads(result[0].text)


@pytest.fixture(autouse=True)
def isolated_context_store(monkeypatch):
    store = ContextStore()
    monkeypatch.setattr(server, "context_store", store)
    return store


def test_budget_policy_uses_pilot_defaults():
    policy = BudgetPolicy()

    assert policy.max_calls_per_cycle == 10
    assert policy.max_onshape_reads_per_cycle == 6
    assert policy.max_consecutive_reads == 4
    assert policy.max_inline_bytes == 32768
    assert policy.max_feature_rows == 50


@pytest.mark.asyncio
async def test_pilot_tools_are_registered_with_bounded_inputs():
    tools = {tool.name: tool for tool in await list_tools()}

    assert {"start_model_context", "get_feature_tree_compact", "get_context_status"} <= tools.keys()
    tree_schema = tools["get_feature_tree_compact"].inputSchema
    assert tree_schema["properties"]["limit"]["default"] == 25
    assert tree_schema["properties"]["limit"]["maximum"] == 50


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_start_returns_compact_l0_and_stores_raw_server_side(
    mock_partstudio, isolated_context_store
):
    raw = _feature_payload()
    mock_partstudio.get_features = AsyncMock(return_value=raw)

    payload = _decode(
        await call_tool(
            "start_model_context",
            {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
        )
    )

    mock_partstudio.get_features.assert_awaited_once_with("doc", "ws", "el")
    assert payload["level"] == "L0"
    assert payload["summary"] == {
        "feature_count": 3,
        "status_counts": {"ERROR": 1, "OK": 2},
        "suppressed_count": 1,
        "error_count": 1,
        "warning_count": 0,
        "revision_id": None,
    }
    assert payload["data"] == {}
    assert payload["truncated"] is False
    assert payload["cost"]["inline_bytes"] <= 32768
    assert "raw-payload-marker" not in json.dumps(payload)

    stored = isolated_context_store.get(payload["context_handle"])
    assert stored.raw_features is raw
    assert stored.working_state.revision_id is None


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_compact_tree_is_cache_only_paged_filtered_and_capped(mock_partstudio):
    raw = _feature_payload(75)
    mock_partstudio.get_features = AsyncMock(return_value=raw)
    started = _decode(
        await call_tool(
            "start_model_context",
            {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
        )
    )
    handle = started["context_handle"]

    first = _decode(
        await call_tool(
            "get_feature_tree_compact",
            {"contextHandle": handle, "offset": 0, "limit": 500},
        )
    )
    filtered = _decode(
        await call_tool(
            "get_feature_tree_compact",
            {"contextHandle": handle, "status": "ERROR", "query": "Feature 2"},
        )
    )
    status = _decode(await call_tool("get_context_status", {"contextHandle": handle}))

    assert first["level"] == "L1"
    assert first["data"]["total"] == 75
    assert first["data"]["returned"] == 50
    assert first["data"]["has_more"] is True
    assert len(first["data"]["rows"]) == 50
    assert first["data"]["rows"][0] == {
        "ordinal": 1,
        "featureId": "feature-0",
        "name": "Feature 0",
        "featureType": "newSketch",
        "status": "OK",
        "suppressed": False,
    }
    assert filtered["data"]["total"] == 1
    assert filtered["data"]["rows"][0]["featureId"] == "feature-2"
    assert mock_partstudio.get_features.await_count == 1
    assert status["data"]["budget"]["used"]["onshape_reads"] == 1
    assert status["data"]["ledger"] == {"onshape_reads": 1, "cache_reads": 2}
    assert "features" not in status["data"]


@pytest.mark.asyncio
async def test_unknown_context_returns_typed_concise_error():
    payload = _decode(await call_tool("get_context_status", {"contextHandle": "missing"}))

    assert payload == {
        "error": {
            "type": "context_not_found",
            "message": "Unknown context handle",
            "context_handle": "missing",
        }
    }


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_oversized_compact_tree_is_reduced_before_serialization(mock_partstudio):
    mock_partstudio.get_features = AsyncMock(return_value=_feature_payload(50, name_size=2000))
    started = _decode(
        await call_tool(
            "start_model_context",
            {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
        )
    )

    result = await call_tool(
        "get_feature_tree_compact",
        {"contextHandle": started["context_handle"], "limit": 50},
    )
    raw_text = result[0].text
    payload = json.loads(raw_text)

    assert len(raw_text.encode("utf-8")) <= 32768
    assert payload["cost"]["inline_bytes"] == len(raw_text.encode("utf-8"))
    assert payload["truncated"] is True
    assert payload["data"]["returned"] < 50
    assert payload["data"]["has_more"] is True
