"""Focused server contracts for the WP-002 cached deep-read vertical slice."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import onshape_mcp.server as server
from onshape_mcp.governance import BudgetPolicy, ContextStore
from onshape_mcp.server import call_tool, list_tools


def _decode(result) -> dict:
    assert len(result) == 1
    return json.loads(result[0].text)


def _deep_payload() -> dict:
    return {
        "sourceMicroversion": "microversion-1",
        "features": [
            {
                "btType": "BTMSketch-151",
                "featureId": "sketch-1",
                "featureType": "newSketch",
                "name": "Base Profile",
                "suppressed": False,
                "parameters": [
                    {
                        "btType": "BTMParameterQuantity-147",
                        "parameterId": "width",
                        "expression": "25 mm",
                        "value": 0.025,
                        "units": "m",
                    },
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "plane",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["JDC"],
                            }
                        ],
                    },
                    {
                        "btType": "BTMParameterUnknown-999",
                        "parameterId": "unknown",
                        "payload": {"raw-payload-marker": "server-side-only"},
                    },
                ],
            },
            {
                "btType": "BTMFeature-134",
                "featureId": "extrude-1",
                "featureType": "extrude",
                "name": "Main Extrude",
                "suppressed": False,
                "parameters": [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "entities",
                        "queries": [
                            {
                                "btType": "BTMIndividualSketchRegionQuery-140",
                                "featureId": "sketch-1",
                                "deterministicIds": ["JGC"],
                            }
                        ],
                    }
                ],
            },
            {
                "btType": "BTMFeature-134",
                "featureId": "fillet-1",
                "featureType": "fillet",
                "name": "Edge Finish",
                "suppressed": False,
                "parameters": [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "entities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["edge-JHD"],
                            }
                        ],
                    }
                ],
            },
        ],
        "featureStates": {
            "sketch-1": {"featureStatus": "OK"},
            "extrude-1": {"featureStatus": "OK"},
            "fillet-1": {"featureStatus": "WARNING"},
        },
    }


@pytest.fixture(autouse=True)
def isolated_context_store(monkeypatch):
    store = ContextStore()
    monkeypatch.setattr(server, "context_store", store)
    return store


async def _start_context(mock_partstudio: object) -> str:
    mock_partstudio.get_features = AsyncMock(return_value=_deep_payload())
    started = _decode(
        await call_tool(
            "start_model_context",
            {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
        )
    )
    return started["context_handle"]


@pytest.mark.asyncio
async def test_deep_read_tools_register_with_hard_schema_limits() -> None:
    tools = {tool.name: tool for tool in await list_tools()}

    assert {
        "start_model_context",
        "get_feature_tree_compact",
        "get_context_status",
        "find_features",
        "inspect_feature",
        "inspect_feature_dependencies",
        "get_dependency_slice",
        "update_working_state",
    } <= tools.keys()
    assert tools["find_features"].inputSchema["properties"]["limit"]["maximum"] == 50
    assert (
        tools["inspect_feature"].inputSchema["properties"]["maxParameters"]["maximum"]
        == 80
    )
    slice_properties = tools["get_dependency_slice"].inputSchema["properties"]
    assert slice_properties["depth"]["maximum"] == 3
    assert slice_properties["maxNodes"]["maximum"] == 80
    assert slice_properties["maxEdges"]["maximum"] == 160
    update_schema = tools["update_working_state"].inputSchema
    assert update_schema["additionalProperties"] is False
    assert update_schema["properties"]["focusFeatureIds"]["maxItems"] == 12
    assert update_schema["properties"]["hypotheses"]["maxItems"] == 8
    assert update_schema["properties"]["evidenceRefs"]["maxItems"] == 20


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_all_deep_reads_reuse_one_cached_onboarding_payload(
    mock_partstudio, isolated_context_store
) -> None:
    handle = await _start_context(mock_partstudio)

    found = _decode(
        await call_tool(
            "find_features",
            {"contextHandle": handle, "query": "main", "limit": 500},
        )
    )
    inspected = _decode(
        await call_tool(
            "inspect_feature",
            {"contextHandle": handle, "featureId": "extrude-1"},
        )
    )
    dependencies = _decode(
        await call_tool(
            "inspect_feature_dependencies",
            {"contextHandle": handle, "featureId": "fillet-1"},
        )
    )
    sliced = _decode(
        await call_tool(
            "get_dependency_slice",
            {
                "contextHandle": handle,
                "featureId": "extrude-1",
                "direction": "both",
                "depth": 3,
                "maxNodes": 500,
                "maxEdges": 500,
            },
        )
    )
    status = _decode(await call_tool("get_context_status", {"contextHandle": handle}))

    assert found["level"] == "L1"
    assert found["data"]["rows"][0]["featureId"] == "extrude-1"
    assert found["data"]["returned"] == 1
    assert found["cache"]["index_hit"] is False
    assert inspected["level"] == "L3"
    assert inspected["data"]["feature"]["featureId"] == "extrude-1"
    assert inspected["cache"]["index_hit"] is True
    assert dependencies["level"] == "L3"
    assert dependencies["data"]["unresolved_references"][0]["evidenceType"] == (
        "UNRESOLVED_GEOMETRY_REFERENCE"
    )
    assert sliced["level"] == "L2"
    assert sliced["data"]["limits"] == {"depth": 3, "maxNodes": 80, "maxEdges": 160}
    assert mock_partstudio.get_features.await_count == 1
    assert status["data"]["ledger"] == {"onshape_reads": 1, "cache_reads": 4}
    assert status["data"]["budget"]["used"]["onshape_reads"] == 1
    assert status["data"]["budget"]["used"]["exploratory_reads"] == 4
    for payload in (found, inspected, dependencies, sliced, status):
        assert "raw-payload-marker" not in json.dumps(payload)
        assert payload["cost"]["inline_bytes"] <= 32768


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_identical_cached_requests_are_fingerprinted_without_new_onshape_read(
    mock_partstudio,
) -> None:
    handle = await _start_context(mock_partstudio)
    arguments = {"contextHandle": handle, "query": "profile", "limit": 20}

    first = _decode(await call_tool("find_features", arguments))
    repeated = _decode(await call_tool("find_features", arguments))
    status = _decode(await call_tool("get_context_status", {"contextHandle": handle}))

    assert first["cache"]["cache_repeat"] is False
    assert repeated["cache"]["cache_repeat"] is True
    assert mock_partstudio.get_features.await_count == 1
    assert status["data"]["ledger"]["onshape_reads"] == 1
    assert status["data"]["ledger"]["cache_reads"] == 2


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_missing_feature_returns_typed_bounded_envelope(mock_partstudio) -> None:
    handle = await _start_context(mock_partstudio)

    payload = _decode(
        await call_tool(
            "inspect_feature",
            {"contextHandle": handle, "featureId": "missing"},
        )
    )

    assert payload["level"] == "L3"
    assert payload["error"] == {
        "type": "FEATURE_NOT_FOUND",
        "message": "Feature not found in cached model context",
        "feature_id": "missing",
    }
    assert payload["cost"]["inline_bytes"] <= 32768


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_working_state_updates_only_bounded_operational_fields(
    mock_partstudio, isolated_context_store
) -> None:
    handle = await _start_context(mock_partstudio)

    forbidden = _decode(
        await call_tool(
            "update_working_state",
            {"contextHandle": handle, "budget": {"calls_used": 0}},
        )
    )
    updated = _decode(
        await call_tool(
            "update_working_state",
            {
                "contextHandle": handle,
                "focusFeatureIds": ["extrude-1"],
                "hypotheses": [
                    {
                        "hypothesisId": "h1",
                        "claim": "Main Extrude consumes the Base Profile sketch",
                        "status": "open",
                        "evidenceRefs": ["obs-1"],
                    }
                ],
                "evidenceRefs": ["obs-1"],
                "nextAction": {
                    "capability": "inspect_feature_dependencies",
                    "targets": ["extrude-1"],
                },
                "phase": "investigate",
            },
        )
    )
    too_many_focus = _decode(
        await call_tool(
            "update_working_state",
            {"contextHandle": handle, "focusFeatureIds": [f"f-{i}" for i in range(13)]},
        )
    )
    too_long_claim = _decode(
        await call_tool(
            "update_working_state",
            {
                "contextHandle": handle,
                "hypotheses": [
                    {"hypothesisId": "h2", "claim": "x" * 501, "status": "open"}
                ],
            },
        )
    )
    stored = isolated_context_store.get(handle).working_state

    assert forbidden["error"]["type"] == "WORKING_STATE_FIELD_FORBIDDEN"
    assert updated["data"]["meaningful_update"] is True
    assert updated["data"]["working_state"] == {
        "phase": "investigate",
        "focus_feature_ids": ["extrude-1"],
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "claim": "Main Extrude consumes the Base Profile sketch",
                "status": "open",
                "evidence_refs": ["obs-1"],
            }
        ],
        "evidence_refs": ["obs-1"],
        "next_action": {
            "capability": "inspect_feature_dependencies",
            "targets": ["extrude-1"],
        },
    }
    assert too_many_focus["error"]["type"] == "WORKING_STATE_INVALID"
    assert too_long_claim["error"]["type"] == "WORKING_STATE_INVALID"
    assert stored.model_ref.to_dict() == {
        "document_id": "doc",
        "workspace_id": "ws",
        "element_id": "el",
    }
    assert stored.revision_id == "microversion-1"
    assert stored.budget.onshape_reads_used == 1


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_consecutive_read_gate_requires_meaningful_narrowing_state(mock_partstudio) -> None:
    handle = await _start_context(mock_partstudio)

    for ordinal in range(4):
        allowed = _decode(
            await call_tool(
                "find_features",
                {"contextHandle": handle, "fromOrdinal": ordinal + 1},
            )
        )
        assert "error" not in allowed

    gated = _decode(
        await call_tool("find_features", {"contextHandle": handle, "query": "extrude"})
    )
    no_op = _decode(await call_tool("update_working_state", {"contextHandle": handle}))
    still_gated = _decode(
        await call_tool("inspect_feature", {"contextHandle": handle, "featureId": "extrude-1"})
    )
    targeted = _decode(
        await call_tool(
            "update_working_state",
            {
                "contextHandle": handle,
                "focusFeatureIds": ["extrude-1"],
                "hypotheses": [
                    {"hypothesisId": "h1", "claim": "Check exact inputs", "status": "open"}
                ],
            },
        )
    )
    continued = _decode(
        await call_tool("inspect_feature", {"contextHandle": handle, "featureId": "extrude-1"})
    )

    assert gated["error"]["type"] == "HYPOTHESIS_REQUIRED"
    assert no_op["data"]["meaningful_update"] is False
    assert still_gated["error"]["type"] == "HYPOTHESIS_REQUIRED"
    assert targeted["data"]["meaningful_update"] is True
    assert continued["data"]["feature"]["featureId"] == "extrude-1"


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_call_budget_exhaustion_is_typed_and_compact(mock_partstudio, monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "context_store",
        ContextStore(
            BudgetPolicy(
                max_calls_per_cycle=2,
                max_exploratory_reads_per_cycle=10,
            )
        ),
    )
    handle = await _start_context(mock_partstudio)

    assert "error" not in _decode(
        await call_tool("find_features", {"contextHandle": handle, "query": "profile"})
    )
    exhausted = _decode(
        await call_tool("find_features", {"contextHandle": handle, "query": "extrude"})
    )

    assert exhausted["error"]["type"] == "CALL_BUDGET_EXHAUSTED"
    assert len(json.dumps(exhausted).encode("utf-8")) <= 32768


@pytest.mark.asyncio
@patch("onshape_mcp.server.partstudio_manager")
async def test_read_budget_exhaustion_is_typed_and_does_not_touch_onshape(
    mock_partstudio, monkeypatch
) -> None:
    monkeypatch.setattr(
        server,
        "context_store",
        ContextStore(
            BudgetPolicy(
                max_calls_per_cycle=20,
                max_exploratory_reads_per_cycle=2,
            )
        ),
    )
    handle = await _start_context(mock_partstudio)

    for query in ("profile", "extrude"):
        assert "error" not in _decode(
            await call_tool("find_features", {"contextHandle": handle, "query": query})
        )
    exhausted = _decode(
        await call_tool("inspect_feature", {"contextHandle": handle, "featureId": "fillet-1"})
    )

    assert exhausted["error"]["type"] == "READ_BUDGET_EXHAUSTED"
    assert mock_partstudio.get_features.await_count == 1
