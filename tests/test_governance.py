"""Focused tests for the token-governance pilot tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

import onshape_mcp.server as server
from onshape_mcp.governance import BudgetPolicy, ContextStore, GovernanceBudgetError
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


def test_authoritative_feature_delta_refreshes_only_matching_context():
    store = ContextStore()
    target = store.create("doc", "ws", "part-studio")
    unrelated = store.create("other-doc", "other-ws", "other-element")
    original = _feature_payload(2)
    other_original = _feature_payload(1)
    store.store_features(target.working_state.context_handle, original)
    store.store_features(unrelated.working_state.context_handle, other_original)
    store.get_feature_index(target.working_state.context_handle)

    summary = store.apply_authoritative_feature_delta(
        "doc",
        "ws",
        "part-studio",
        {
            "features": [
                {
                    **original["features"][0],
                    "name": "Updated feature",
                }
            ],
            "featureStates": {"feature-0": {"featureStatus": "OK"}},
            "sourceMicroversion": "microversion-2",
        },
    )

    refreshed = store.get(target.working_state.context_handle)
    assert summary == {
        "contexts_updated": 1,
        "feature_ids": ["feature-0"],
        "feature_cache": "partially_refreshed",
        "topology_cache": "invalidated",
    }
    assert refreshed.working_state.model_ref.document_id == "doc"
    assert refreshed.working_state.model_ref.workspace_id == "ws"
    assert refreshed.working_state.model_ref.element_id == "part-studio"
    assert refreshed.working_state.revision_id is None
    assert [item["name"] for item in refreshed.raw_features["features"]] == [
        "Updated feature",
        "Feature 1",
    ]
    assert refreshed.feature_index is None
    assert refreshed.cache_metadata["state"] == "partial"
    assert refreshed.cache_metadata["index_state"] == "empty"
    assert refreshed.cache_metadata["topology_state"] == "stale"
    assert refreshed.cache_metadata["invalidations"] == 1
    assert refreshed.cache_metadata["fresh_feature_ids"] == ["feature-0"]
    assert refreshed.cache_metadata["delta_revision_id"] == "microversion-2"
    assert store.get(unrelated.working_state.context_handle).raw_features is other_original


def test_authoritative_created_feature_is_added_without_discarding_cached_tree():
    store = ContextStore()
    record = store.create("doc", "ws", "part-studio")
    original = _feature_payload(1)
    store.store_features(record.working_state.context_handle, original)

    store.apply_authoritative_feature_delta(
        "doc",
        "ws",
        "part-studio",
        {
            "features": [
                {
                    "featureId": "feature-created",
                    "featureType": "shell",
                    "name": "Shell",
                    "parameters": [],
                }
            ],
            "featureStates": {"feature-created": {"featureStatus": "OK"}},
        },
        created_feature_ids=["feature-created"],
    )

    refreshed = store.get(record.working_state.context_handle)
    assert [item["featureId"] for item in refreshed.raw_features["features"]] == [
        "feature-0",
        "feature-created",
    ]
    assert refreshed.cache_metadata["feature_count"] == 2
    assert refreshed.cache_metadata["state"] == "ready"


def test_authoritative_topology_refresh_clears_only_matching_stale_marker():
    store = ContextStore()
    target = store.create("doc", "ws", "part-studio")
    other = store.create("other", "ws", "part-studio")
    store.store_features(target.working_state.context_handle, _feature_payload(1))
    store.store_features(other.working_state.context_handle, _feature_payload(1))
    store.invalidate_model("doc", "ws", "part-studio")

    updated = store.mark_topology_fresh("doc", "ws", "part-studio")

    assert updated == 1
    assert store.get(target.working_state.context_handle).cache_metadata[
        "topology_state"
    ] == "fresh"
    assert store.get(other.working_state.context_handle).cache_metadata[
        "topology_state"
    ] == "unknown"


def test_invalidated_feature_context_cannot_serve_stale_index():
    store = ContextStore()
    record = store.create("doc", "ws", "part-studio")
    store.store_features(record.working_state.context_handle, _feature_payload(1))
    store.get_feature_index(record.working_state.context_handle)
    store.invalidate_model("doc", "ws", "part-studio")

    with pytest.raises(GovernanceBudgetError) as error:
        store.get_feature_index(record.working_state.context_handle)

    assert error.value.error_type == "CONTEXT_STATE_STALE"


def test_full_authoritative_snapshot_removes_deleted_feature_from_context():
    store = ContextStore()
    record = store.create("doc", "ws", "part-studio")
    store.store_features(record.working_state.context_handle, _feature_payload(2))

    summary = store.replace_authoritative_snapshot(
        "doc",
        "ws",
        "part-studio",
        {
            "features": [_feature_payload(2)["features"][1]],
            "featureStates": {"feature-1": {"featureStatus": "OK"}},
            "sourceMicroversion": "after-delete",
        },
        affected_feature_ids=["feature-0"],
    )

    refreshed = store.get(record.working_state.context_handle)
    assert summary == {
        "contexts_updated": 1,
        "feature_ids": ["feature-0"],
        "feature_cache": "refreshed",
        "topology_cache": "invalidated",
    }
    assert [item["featureId"] for item in refreshed.raw_features["features"]] == [
        "feature-1"
    ]
    assert refreshed.working_state.revision_id == "after-delete"
    assert refreshed.cache_metadata["state"] == "ready"
    assert refreshed.cache_metadata["topology_state"] == "stale"


def test_workspace_invalidation_marks_all_matching_part_studio_contexts_stale():
    store = ContextStore()
    first = store.create("doc", "ws", "part-a")
    second = store.create("doc", "ws", "part-b")
    unrelated = store.create("other", "ws", "part-c")
    for record in (first, second, unrelated):
        store.store_features(record.working_state.context_handle, _feature_payload(1))

    updated = store.invalidate_workspace("doc", "ws")

    assert updated == 2
    assert store.get(first.working_state.context_handle).cache_metadata["state"] == "stale"
    assert store.get(second.working_state.context_handle).cache_metadata["state"] == "stale"
    assert store.get(unrelated.working_state.context_handle).cache_metadata["state"] == "ready"


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
