"""Agent-facing execution protocol contracts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from mcp.types import TextContent
import pytest

import onshape_mcp.server as server
from onshape_mcp.execution_protocol import (
    ExecutionProtocolMetrics,
    canonical_request,
    compact_execution_result,
    compact_feature,
    compact_model_state,
)


TARGET = {
    "documentId": "doc-safe",
    "workspaceId": "workspace-safe",
    "elementId": "element-safe",
}


def _legacy_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "status": "OK",
        "feature_id": "extrude-safe",
        "feature_name": "Main Extrude",
        "feature_type": "extrude",
        "error_message": None,
        "transport_ok": True,
        "http_ok": True,
        "regen_ok": True,
        "mutation_verification": "verified",
        "changed": True,
        "verification_scope": "feature_state",
        "reason_code": "REQUESTED_STATE_VERIFIED",
        "verification_message": "Requested feature fields match the authoritative reread.",
        "tool": "create_extrude",
    }
    result.update(overrides)
    return result


def _features_doc() -> dict[str, object]:
    return {
        "sourceMicroversion": "microversion-safe",
        "features": [
            {
                "btType": "BTMSketch-151",
                "featureId": "sketch-safe",
                "featureType": "newSketch",
                "name": "Base Sketch",
                "suppressed": False,
                "parameters": [],
            },
            {
                "btType": "BTMFeature-134",
                "featureId": "extrude-safe",
                "featureType": "extrude",
                "name": "Main Extrude",
                "suppressed": False,
                "parameters": [
                    {
                        "btType": "BTMParameterQuantity-147",
                        "parameterId": "depth",
                        "expression": "27.6 mm",
                        "value": 0.0276,
                        "units": "m",
                    }
                ],
            },
        ],
        "featureStates": {
            "sketch-safe": {"featureStatus": "OK", "messages": []},
            "extrude-safe": {"featureStatus": "OK", "messages": []},
        },
    }


def test_canonical_request_exposes_only_reasoning_parameters() -> None:
    request = {
        **TARGET,
        "name": "Main Extrude",
        "sketchFeatureId": "sketch-safe",
        "depth": "27.6 mm",
        "operationType": "NEW",
        "endType": "BLIND",
        "privatePayload": {"raw": "must-not-cross"},
    }

    assert canonical_request("create_extrude", request) == {
        "name": "Main Extrude",
        "sketch_feature_id": "sketch-safe",
        "depth": "27.6 mm",
        "operation_type": "NEW",
        "end_type": "BLIND",
    }


def test_verified_mutation_returns_compact_semantic_result_without_topology_lists() -> None:
    large_changes = {
        "body_count_before": 1,
        "body_count_after": 1,
        "faces_added": [{"id": f"face-{index}"} for index in range(200)],
        "faces_removed": [{"id": f"old-face-{index}"} for index in range(200)],
        "edges_added": [{"id": f"edge-{index}"} for index in range(400)],
        "edges_removed": [],
        "volume_delta_mm3": 42.5,
        "summary": "volume +42.5 mm3; faces +200/-200; edges +400/-0",
    }
    legacy = _legacy_result(changes=large_changes)
    request = {
        **TARGET,
        "name": "Main Extrude",
        "sketchFeatureId": "sketch-safe",
        "depth": "27.6 mm",
        "operationType": "NEW",
    }

    compact = compact_execution_result("create_extrude", request, legacy)
    rendered = json.dumps(compact)

    assert compact["contract"] == "jarvis.execution_result.v1"
    assert compact["target"] == {
        "document_id": "doc-safe",
        "workspace_id": "workspace-safe",
        "element_id": "element-safe",
    }
    assert compact["feature"] == {
        "id": "extrude-safe",
        "name": "Main Extrude",
        "type": "extrude",
    }
    assert compact["mutation"]["state"] == "verified"
    assert compact["regeneration"] == {"state": "OK", "ok": True}
    assert compact["followup"] == {
        "required": False,
        "reason": "requested_state_verified",
    }
    assert compact["change_summary"] == {
        "summary": "volume +42.5 mm3; faces +200/-200; edges +400/-0",
        "body_count_before": 1,
        "body_count_after": 1,
        "faces_added_count": 200,
        "faces_removed_count": 200,
        "edges_added_count": 400,
        "edges_removed_count": 0,
        "volume_delta_mm3": 42.5,
    }
    assert "face-199" not in rendered
    assert "edge-399" not in rendered
    assert "legacy_result" not in compact
    assert len(rendered.encode("utf-8")) < 4_000


def test_failed_mutation_preserves_blocker_and_recovery_hint() -> None:
    compact = compact_execution_result(
        "create_shell",
        {**TARGET, "thickness": "2.4 mm", "faceIds": ["face-top"]},
        _legacy_result(
            ok=False,
            status="ERROR",
            feature_id="shell-safe",
            feature_name="Shell",
            feature_type="shell",
            error_message="Shell thickness intersects itself.",
            regen_ok=False,
            mutation_verification="failed",
            changed=False,
            reason_code="FEATURE_REGENERATION_ERROR",
            hints=["Choose a smaller thickness and update the same feature."],
        ),
    )

    assert compact["mutation"]["state"] == "failed"
    assert compact["followup"]["required"] is True
    assert compact["blocker"] == {
        "reason_code": "FEATURE_REGENERATION_ERROR",
        "message": "Shell thickness intersects itself.",
        "recovery_hint": "Choose a smaller thickness and update the same feature.",
    }


def test_unverified_mutation_preserves_uncertainty() -> None:
    compact = compact_execution_result(
        "update_feature",
        {
            **TARGET,
            "featureId": "extrude-safe",
            "updates": [{"parameterId": "depth", "expression": "30 mm"}],
        },
        _legacy_result(
            ok=False,
            status="UNKNOWN",
            error_message="Authoritative reread failed.",
            regen_ok=None,
            mutation_verification="unverified",
            changed=None,
            reason_code="AUTHORITATIVE_REREAD_FAILED",
        ),
    )

    assert compact["mutation"] == {
        "state": "unverified",
        "changed": None,
        "transport_ok": True,
        "http_ok": True,
        "verification_scope": "feature_state",
    }
    assert compact["followup"]["required"] is True
    assert compact["blocker"]["message"] == "Authoritative reread failed."


def test_diagnostic_mode_retains_existing_bounded_evidence() -> None:
    legacy = _legacy_result(
        changes={"faces_added": [{"id": "face-safe"}], "summary": "faces +1/-0"}
    )

    diagnostic = compact_execution_result(
        "create_extrude",
        {**TARGET, "sketchFeatureId": "sketch-safe", "depth": "10 mm"},
        legacy,
        response_mode="diagnostic",
    )

    assert diagnostic["diagnostic"]["legacy_result"] == legacy
    assert diagnostic["response_mode"] == "diagnostic"


def test_compact_feature_uses_one_normalized_bounded_projection() -> None:
    feature = compact_feature(_features_doc(), "extrude-safe", max_parameters=4)

    assert feature == {
        "ordinal": 2,
        "feature_id": "extrude-safe",
        "name": "Main Extrude",
        "type": "extrude",
        "status": "OK",
        "suppressed": False,
        "parameter_count": 1,
        "canonical_parameters": {
            "depth": {"expression": "27.6 mm", "value": 0.0276, "units": "m"}
        },
        "references": {
            "confirmed_feature_count": 0,
            "unresolved_reference_count": 0,
            "evidence_types": [],
        },
        "truncated": False,
        "warnings": [],
    }


def test_compact_model_state_preserves_identity_status_and_marks_unread_layers() -> None:
    state = compact_model_state(
        target=TARGET,
        features_doc=_features_doc(),
        parts=[
            {"partId": "body-1", "name": "Enclosure", "partType": "SOLID"},
            {"partId": "body-2", "name": "Lid", "partType": "SOLID"},
        ],
        recent_feature_limit=10,
        max_parameters_per_feature=4,
        changed_feature_ids=["extrude-safe", "missing-feature"],
    )

    assert state["contract"] == "jarvis.compact_model_state.v1"
    assert state["source_revision"] == "microversion-safe"
    assert state["regeneration"] == {
        "state": "OK",
        "feature_count": 2,
        "failure_count": 0,
    }
    assert state["bodies"] == {
        "count": 2,
        "returned": 2,
        "truncated": False,
        "rows": [
            {"body_id": "body-1", "name": "Enclosure", "type": "SOLID"},
            {"body_id": "body-2", "name": "Lid", "type": "SOLID"},
        ],
    }
    assert state["features"]["rows"][-1]["canonical_parameters"]["depth"][
        "expression"
    ] == "27.6 mm"
    assert state["changed_feature_ids"] == ["extrude-safe"]
    assert state["unknown_or_stale"] == [
        {
            "category": "topology",
            "state": "not_loaded",
            "reason": "Compact model state does not read body topology.",
        },
        {
            "category": "sketch_constraints",
            "state": "unavailable",
            "reason": "No authoritative compact constraint status was present.",
        },
    ]
    assert "RAW" not in json.dumps(state)


def test_metrics_count_outer_operations_and_response_families() -> None:
    metrics = ExecutionProtocolMetrics()
    metrics.record(
        "execute_feature",
        {**TARGET, "operation": "create_extrude"},
        response_bytes=1200,
    )
    metrics.record("describe_part_studio", TARGET, response_bytes=9000)
    metrics.record("list_entities", TARGET, response_bytes=5000)

    snapshot = metrics.snapshot()

    assert snapshot["mcp_tool_invocations"] == 3
    assert snapshot["logical_operations"] == 1
    assert snapshot["response_bytes"] == 15_200
    assert snapshot["largest_response"] == {
        "tool": "describe_part_studio",
        "bytes": 9000,
    }
    assert snapshot["verification_followup_proxy"] == 1
    assert snapshot["target_resolution_call_proxy"] == 1
    assert snapshot["by_family"]["mutation"] == {
        "calls": 1,
        "response_bytes": 1200,
        "max_response_bytes": 1200,
    }


@pytest.mark.asyncio
async def test_preferred_protocol_tools_have_bounded_schemas() -> None:
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert {
        "execute_feature",
        "inspect_feature_compact",
        "get_compact_model_state",
        "get_execution_protocol_metrics",
    } <= tools.keys()
    execute_schema = tools["execute_feature"].inputSchema
    assert execute_schema["additionalProperties"] is False
    assert execute_schema["properties"]["operation"]["enum"] == [
        "create_sketch",
        "create_extrude",
        "update_feature",
        "create_linear_pattern",
        "create_shell",
        "create_chamfer",
        "create_draft",
        "move_body",
    ]
    assert execute_schema["properties"]["responseMode"]["enum"] == [
        "compact",
        "diagnostic",
    ]
    assert execute_schema["required"] == ["operation", "request"]


@pytest.mark.asyncio
async def test_execute_feature_delegates_exactly_once_and_returns_same_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegated = AsyncMock(
        return_value=[TextContent(type="text", text=json.dumps(_legacy_result()))]
    )
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)
    request = {
        **TARGET,
        "name": "Main Extrude",
        "sketchFeatureId": "sketch-safe",
        "depth": "27.6 mm",
        "operationType": "NEW",
    }

    response = await server.call_tool(
        "execute_feature",
        {"operation": "create_extrude", "request": request},
    )
    payload = json.loads(response[0].text)

    delegated.assert_awaited_once_with("create_extrude", request)
    assert payload["feature"]["id"] == "extrude-safe"
    assert payload["requested_parameters"]["depth"] == "27.6 mm"
    assert payload["mutation"]["state"] == "verified"
    assert payload["followup"]["required"] is False


@pytest.mark.asyncio
async def test_execute_feature_diagnostic_mode_retains_legacy_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _legacy_result(
        changes={"summary": "faces +1/-0", "faces_added": [{"id": "face-safe"}]}
    )
    delegated = AsyncMock(
        return_value=[TextContent(type="text", text=json.dumps(legacy))]
    )
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)

    response = await server.call_tool(
        "execute_feature",
        {
            "operation": "create_extrude",
            "request": {**TARGET, "sketchFeatureId": "sketch-safe", "depth": "10 mm"},
            "responseMode": "diagnostic",
        },
    )
    payload = json.loads(response[0].text)

    assert payload["diagnostic"]["legacy_result"] == legacy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verification", "reason_code"),
    [
        ("failed", "FEATURE_REGENERATION_ERROR"),
        ("unverified", "AUTHORITATIVE_REREAD_FAILED"),
    ],
)
async def test_execute_feature_preserves_non_success_state_and_blocker(
    monkeypatch: pytest.MonkeyPatch,
    verification: str,
    reason_code: str,
) -> None:
    delegated = AsyncMock(
        return_value=[
            TextContent(
                type="text",
                text=json.dumps(
                    _legacy_result(
                        ok=False,
                        status="ERROR" if verification == "failed" else "UNKNOWN",
                        regen_ok=False if verification == "failed" else None,
                        mutation_verification=verification,
                        error_message="Authoritative evidence did not prove the request.",
                        reason_code=reason_code,
                    )
                ),
            )
        ]
    )
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)

    response = await server.call_tool(
        "execute_feature",
        {
            "operation": "update_feature",
            "request": {
                **TARGET,
                "featureId": "extrude-safe",
                "updates": [{"parameterId": "depth", "expression": "30 mm"}],
            },
        },
    )
    payload = json.loads(response[0].text)

    assert payload["mutation"]["state"] == verification
    assert payload["blocker"]["reason_code"] == reason_code
    assert payload["followup"]["required"] is True


@pytest.mark.asyncio
async def test_execute_feature_rejects_nested_or_unsupported_operation_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegated = AsyncMock()
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)

    response = await server.call_tool(
        "execute_feature",
        {"operation": "execute_feature", "request": TARGET},
    )
    payload = json.loads(response[0].text)

    delegated.assert_not_awaited()
    assert payload["ok"] is False
    assert payload["mutation_verification"] == "failed"
    assert payload["reason_code"] == "LOCAL_MUTATION_REJECTED"


@pytest.mark.asyncio
async def test_inspect_feature_compact_requires_one_authoritative_feature_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_features = AsyncMock(return_value=_features_doc())
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)

    response = await server.call_tool(
        "inspect_feature_compact",
        {**TARGET, "featureId": "extrude-safe", "maxParameters": 4},
    )
    payload = json.loads(response[0].text)

    get_features.assert_awaited_once_with("doc-safe", "workspace-safe", "element-safe")
    assert payload["contract"] == "jarvis.compact_feature.v1"
    assert payload["feature"]["feature_id"] == "extrude-safe"
    assert payload["feature"]["canonical_parameters"]["depth"]["expression"] == "27.6 mm"
    assert "features" not in payload


@pytest.mark.asyncio
async def test_get_compact_model_state_reads_features_and_parts_without_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_features = AsyncMock(return_value=_features_doc())
    get_parts = AsyncMock(return_value=[{"partId": "body-1", "name": "Enclosure"}])
    get_body_details = AsyncMock()
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)
    monkeypatch.setattr(server.partstudio_manager, "get_parts", get_parts)
    monkeypatch.setattr(server.partstudio_manager, "get_body_details", get_body_details)

    response = await server.call_tool(
        "get_compact_model_state",
        {
            **TARGET,
            "recentFeatureLimit": 10,
            "maxParametersPerFeature": 4,
            "changedFeatureIds": ["extrude-safe"],
        },
    )
    payload = json.loads(response[0].text)

    get_features.assert_awaited_once_with("doc-safe", "workspace-safe", "element-safe")
    get_parts.assert_awaited_once_with("doc-safe", "workspace-safe", "element-safe")
    get_body_details.assert_not_awaited()
    assert payload["contract"] == "jarvis.compact_model_state.v1"
    assert payload["bodies"]["count"] == 1
    assert payload["changed_feature_ids"] == ["extrude-safe"]
    assert payload["unknown_or_stale"][0]["category"] == "topology"


@pytest.mark.asyncio
async def test_execution_protocol_metrics_tool_returns_bounded_snapshot() -> None:
    response = await server.call_tool("get_execution_protocol_metrics", {})
    payload = json.loads(response[0].text)

    assert payload["contract"] == "jarvis.execution_protocol_metrics.v1"
    assert isinstance(payload["mcp_tool_invocations"], int)
    assert "proxy_definitions" in payload


@pytest.mark.asyncio
async def test_outer_execute_feature_call_is_counted_once_by_response_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh_metrics = ExecutionProtocolMetrics()
    monkeypatch.setattr(server, "execution_protocol_metrics", fresh_metrics)
    legacy = _legacy_result(
        changes={
            "summary": "faces +250/-250; edges +500/-500",
            "faces_added": [{"id": f"face-new-{index}"} for index in range(250)],
            "faces_removed": [{"id": f"face-old-{index}"} for index in range(250)],
            "edges_added": [{"id": f"edge-new-{index}"} for index in range(500)],
            "edges_removed": [{"id": f"edge-old-{index}"} for index in range(500)],
        }
    )
    delegated = AsyncMock(
        return_value=[TextContent(type="text", text=json.dumps(legacy))]
    )
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)

    compact_response = await server.call_tool(
        "execute_feature",
        {
            "operation": "create_extrude",
            "request": {**TARGET, "sketchFeatureId": "sketch-safe", "depth": "10 mm"},
        },
    )
    compact_bytes = len(compact_response[0].text.encode("utf-8"))
    snapshot = fresh_metrics.snapshot()

    delegated.assert_awaited_once()
    assert snapshot["mcp_tool_invocations"] == 1
    assert snapshot["model_tool_round_trip_proxy"] == 1
    assert snapshot["logical_operations"] == 1
    assert snapshot["by_family"]["mutation"]["calls"] == 1
    assert snapshot["by_response_mode"]["compact"] == {
        "calls": 1,
        "response_bytes": compact_bytes,
        "max_response_bytes": compact_bytes,
    }
    assert compact_bytes < 4_000
    assert "face-new-249" not in compact_response[0].text
