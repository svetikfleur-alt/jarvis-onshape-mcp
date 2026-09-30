"""Agent-facing execution protocol contracts."""

from __future__ import annotations

from copy import deepcopy
from importlib import metadata
import json
from unittest.mock import AsyncMock

import httpx
from mcp.types import TextContent
import pytest

import onshape_mcp.server as server
from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.entities import EntityManager
from onshape_mcp.execution_protocol import (
    ExecutionProtocolMetrics,
    canonical_request,
    compact_execution_result,
    compact_feature,
    compact_model_state,
)
from onshape_mcp.governance import ContextStore
from onshape_mcp.governance.metrics import ExecutionMetrics


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


def _features_doc(
    *,
    depth_expression: str = "27.6 mm",
    depth_value: float = 0.0276,
    source_microversion: str = "microversion-safe",
) -> dict[str, object]:
    return {
        "sourceMicroversion": source_microversion,
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
                        "expression": depth_expression,
                        "value": depth_value,
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


def _targeted_feature_doc(full_snapshot: dict[str, object]) -> dict[str, object]:
    features = full_snapshot["features"]
    assert isinstance(features, list)
    target = next(
        feature
        for feature in features
        if isinstance(feature, dict) and feature.get("featureId") == "extrude-safe"
    )
    states = full_snapshot["featureStates"]
    assert isinstance(states, dict)
    return {
        "sourceMicroversion": full_snapshot["sourceMicroversion"],
        "features": [deepcopy(target)],
        "featureStates": {"extrude-safe": deepcopy(states["extrude-safe"])},
    }


def _install_integrated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    client: OnshapeClient,
    metrics: ExecutionMetrics,
    cached_snapshot: dict[str, object],
) -> dict[str, object]:
    store = ContextStore(metrics=metrics)
    record = store.create("doc-safe", "workspace-safe", "element-safe")
    store.store_features(record.working_state.context_handle, cached_snapshot)
    index, _ = store.get_feature_index(record.working_state.context_handle)
    store.mark_topology_fresh("doc-safe", "workspace-safe", "element-safe")

    entities = EntityManager(client, metrics=metrics)
    topology_key = ("doc-safe", "workspace-safe", "element-safe")
    entities._bodydetails_cache[topology_key] = {"marker": "cached bodydetails"}
    entities._face_frames_cache[topology_key] = {"marker": "cached face frames"}

    protocol_metrics = ExecutionProtocolMetrics()
    monkeypatch.setattr(server, "client", client)
    monkeypatch.setattr(server, "execution_metrics", metrics)
    monkeypatch.setattr(server, "execution_protocol_metrics", protocol_metrics)
    monkeypatch.setattr(server, "context_store", store)
    monkeypatch.setattr(server, "entity_manager", entities)
    return {
        "record": record,
        "index": index,
        "entities": entities,
        "topology_key": topology_key,
        "protocol_metrics": protocol_metrics,
    }


async def _call_compact_verification_read(
    monkeypatch: pytest.MonkeyPatch,
    *,
    features_doc: dict[str, object] | None = None,
) -> None:
    get_features = AsyncMock(return_value=features_doc or _features_doc())
    get_parts = AsyncMock(return_value=[])
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)
    monkeypatch.setattr(server.partstudio_manager, "get_parts", get_parts)

    response = await server.call_tool("get_compact_model_state", TARGET)
    payload = json.loads(response[0].text)

    assert payload["contract"] == "jarvis.compact_model_state.v1"
    get_features.assert_awaited_once_with("doc-safe", "workspace-safe", "element-safe")
    get_parts.assert_awaited_once_with("doc-safe", "workspace-safe", "element-safe")


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
        "reason_code": "AUTHORITATIVE_REREAD_FAILED",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "operation_request"),
    [
        (
            "create_shell",
            {**TARGET, "thickness": "2 mm", "faceIds": ["face-top"], "outward": "false"},
        ),
        (
            "create_shell",
            {**TARGET, "thickness": "2 mm", "faceIds": "face-top"},
        ),
        (
            "create_extrude",
            {
                **TARGET,
                "sketchFeatureId": "sketch-safe",
                "depth": "10 mm",
                "operationType": "SIDEWAYS",
            },
        ),
        (
            "create_extrude",
            {**TARGET, "sketchFeatureId": "sketch-safe"},
        ),
    ],
)
async def test_execute_feature_validates_selected_tool_schema_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    operation_request: dict[str, object],
) -> None:
    apply = AsyncMock(return_value=server.FeatureApplyResult(
        ok=True,
        status="OK",
        feature_id="must-not-exist",
        feature_name="Must not mutate",
        feature_type="test",
        transport_ok=True,
        http_ok=True,
        regen_ok=True,
        mutation_verification="verified",
        changed=True,
    ))
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    response = await server.call_tool(
        "execute_feature",
        {"operation": operation, "request": operation_request},
    )
    payload = json.loads(response[0].text)

    apply.assert_not_awaited()
    assert payload["tool"] == "execute_feature"
    assert payload["mutation_verification"] == "failed"
    assert payload["changed"] is False


@pytest.mark.asyncio
async def test_execute_feature_preflight_rejection_does_not_create_pending_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_metrics = ExecutionProtocolMetrics()
    perf_metrics = ExecutionMetrics()
    delegated = AsyncMock()
    monkeypatch.setattr(server, "execution_protocol_metrics", protocol_metrics)
    monkeypatch.setattr(server, "execution_metrics", perf_metrics)
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)

    response = await server.call_tool(
        "execute_feature",
        {
            "operation": "create_shell",
            "request": {
                **TARGET,
                "thickness": "2 mm",
                "faceIds": "face-top",
            },
        },
    )
    payload = json.loads(response[0].text)
    mutation_snapshot = protocol_metrics.snapshot()

    delegated.assert_not_awaited()
    assert payload["request_rejected"] is True
    assert mutation_snapshot["mcp_tool_invocations"] == 1
    assert mutation_snapshot["model_tool_round_trip_proxy"] == 1
    assert mutation_snapshot["logical_operations"] == 1

    await _call_compact_verification_read(monkeypatch)

    snapshot = protocol_metrics.snapshot()
    assert snapshot["verification_followup_proxy"] == 0
    assert snapshot["logical_operations"] == 1


def test_compact_model_state_caps_and_bounds_failure_rows() -> None:
    feature_count = 250
    long_tail = "x" * 10_000
    features_doc = {
        "sourceMicroversion": "microversion-many-failures",
        "features": [
            {
                "btType": "BTMFeature-134",
                "featureId": f"failure-{index}",
                "featureType": "extrude",
                "name": f"Failure {index} {long_tail}",
                "parameters": [],
            }
            for index in range(feature_count)
        ],
        "featureStates": {
            f"failure-{index}": {"featureStatus": "ERROR", "messages": []}
            for index in range(feature_count)
        },
    }

    state = compact_model_state(
        target=TARGET,
        features_doc=features_doc,
        parts=[],
        recent_feature_limit=1,
    )
    rendered = json.dumps(state)

    assert state["regeneration"]["failure_count"] == feature_count
    assert state["failures"]["total"] == feature_count
    assert state["failures"]["returned"] == 50
    assert state["failures"]["truncated"] is True
    assert len(state["failures"]["rows"][0]["name"]) <= 256
    assert len(rendered.encode("utf-8")) < 40_000


def test_compact_result_bounds_deeply_nested_requested_values() -> None:
    nested: object = "leaf-" + ("z" * 250)
    for _ in range(4):
        nested = [nested for _ in range(16)]
    compact = compact_execution_result(
        "update_feature",
        {
            **TARGET,
            "featureId": "extrude-safe",
            "updates": [{"parameterId": "custom", "value": nested}],
        },
        _legacy_result(
            ok=False,
            mutation_verification="failed",
            error_message="The requested value was rejected.",
        ),
    )
    rendered = json.dumps(compact)
    bounded_value = compact["requested_parameters"]["updates"][0]["value"]

    assert bounded_value["kind"] == "list"
    assert bounded_value["truncated"] is True
    assert len(rendered.encode("utf-8")) < 8_000


def test_update_request_projection_reports_bounded_row_truncation() -> None:
    projection = canonical_request(
        "update_feature",
        {
            **TARGET,
            "featureId": "extrude-safe",
            "updates": [
                {"parameterId": f"parameter-{index}", "value": index}
                for index in range(33)
            ],
        },
    )

    assert projection["update_count"] == 33
    assert projection["returned_update_count"] == 32
    assert projection["updates_truncated"] is True
    assert len(projection["updates"]) == 32
    assert projection["updates"][-1]["parameter_id"] == "parameter-31"


def test_compact_changes_preserve_parallel_branch_explicit_counts() -> None:
    compact = compact_execution_result(
        "move_body",
        {
            **TARGET,
            "bodyIds": ["body-safe"],
            "translationX": "5 mm",
            "translationY": "0 mm",
            "translationZ": "0 mm",
        },
        _legacy_result(
            feature_id="move-safe",
            feature_name="Move body",
            feature_type="transform",
            changes={
                "summary": "faces +250/-250",
                "faces_added_count": 250,
                "faces_added_sample": [{"id": "face-sample"}],
                "faces_added_truncated": True,
                "edges_removed_count": 400,
                "edges_removed_sample": [{"id": "edge-sample"}],
                "edges_removed_truncated": True,
            },
        ),
    )

    assert compact["change_summary"]["faces_added_count"] == 250
    assert compact["change_summary"]["edges_removed_count"] == 400
    assert "face-sample" not in json.dumps(compact["change_summary"])
    assert "faces_removed_count" not in compact["change_summary"]


def test_actual_invalidation_overrides_static_operation_expectations() -> None:
    compact = compact_execution_result(
        "create_extrude",
        {**TARGET, "sketchFeatureId": "sketch-safe", "depth": "10 mm"},
        _legacy_result(
            invalidation={
                "contexts_updated": 0,
                "feature_ids": ["extrude-safe"],
                "feature_cache": "unchanged",
                "topology_cache": "unchanged",
                "topology_snapshot_evicted": False,
            }
        ),
    )

    assert compact["invalidated_state_categories"] == []
    assert compact["invalidation"] == {
        "basis": "actual",
        "contexts_updated": 0,
        "feature_cache": "unchanged",
        "topology_cache": "unchanged",
        "topology_snapshot_evicted": False,
    }


def test_static_invalidation_is_explicitly_conservative_without_runtime_evidence() -> None:
    compact = compact_execution_result(
        "create_shell",
        {**TARGET, "thickness": "2 mm", "faceIds": ["face-safe"]},
        _legacy_result(),
    )

    assert compact["invalidation"] == {"basis": "conservative_expectation"}
    assert compact["invalidated_state_categories"] == [
        "feature_tree",
        "bodies",
        "topology",
        "render",
    ]


@pytest.mark.parametrize(
    "partial_invalidation",
    [
        {"contexts_updated": 0},
        {"feature_cache": "unchanged"},
        {"topology_cache": "unchanged"},
        {"topology_snapshot_evicted": False},
    ],
)
def test_partial_invalidation_without_complete_cache_truth_keeps_expectations(
    partial_invalidation: dict[str, object],
) -> None:
    compact = compact_execution_result(
        "create_extrude",
        {**TARGET, "sketchFeatureId": "sketch-safe", "depth": "10 mm"},
        _legacy_result(invalidation=partial_invalidation),
    )

    assert compact["invalidation"] == {"basis": "conservative_expectation"}
    assert compact["invalidated_state_categories"] == [
        "feature_tree",
        "bodies",
        "topology",
        "render",
    ]


def test_feature_already_absent_is_terminal_no_effect_without_verification_followup() -> None:
    compact = compact_execution_result(
        "update_feature",
        {
            **TARGET,
            "featureId": "already-absent",
            "updates": [{"parameterId": "depth", "expression": "15 mm"}],
        },
        _legacy_result(
            ok=False,
            status="UNKNOWN",
            feature_id="already-absent",
            feature_name="",
            feature_type="",
            transport_ok=None,
            http_ok=None,
            regen_ok=None,
            mutation_verification="no_effect",
            changed=False,
            reason_code="FEATURE_ALREADY_ABSENT",
            invalidation={
                "contexts_updated": 0,
                "feature_ids": ["already-absent"],
                "feature_cache": "unchanged",
                "topology_cache": "unchanged",
                "topology_snapshot_evicted": False,
            },
        ),
    )

    assert compact["mutation"]["state"] == "no_effect"
    assert compact["mutation"]["reason_code"] == "FEATURE_ALREADY_ABSENT"
    assert compact["followup"] == {
        "required": False,
        "reason": "requested_state_already_satisfied",
    }
    assert "blocker" not in compact


def test_already_present_state_with_regeneration_failure_still_requires_followup() -> None:
    compact = compact_execution_result(
        "update_feature",
        {
            **TARGET,
            "featureId": "broken-feature",
            "updates": [{"parameterId": "depth", "expression": "15 mm"}],
        },
        _legacy_result(
            ok=False,
            status="ERROR",
            feature_id="broken-feature",
            transport_ok=None,
            http_ok=None,
            regen_ok=False,
            mutation_verification="no_effect",
            changed=False,
            reason_code="REQUESTED_STATE_ALREADY_PRESENT",
        ),
    )

    assert compact["followup"]["required"] is True
    assert compact["blocker"]["reason_code"] == "REQUESTED_STATE_ALREADY_PRESENT"


def test_jsonschema_is_declared_as_a_direct_runtime_dependency() -> None:
    requirements = metadata.requires("onshape-mcp") or []

    assert any(
        requirement.partition(";")[0].strip().casefold().startswith("jsonschema")
        for requirement in requirements
    )


@pytest.mark.asyncio
async def test_execute_feature_prewrite_noop_preserves_caches_and_needs_no_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_snapshot = _features_doc()
    targeted = _targeted_feature_doc(cached_snapshot)
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.method == "GET"
        return httpx.Response(200, request=request, json=targeted)

    metrics = ExecutionMetrics()
    credentials = OnshapeCredentials(access_key="test", secret_key="test")
    async with OnshapeClient(
        credentials,
        transport=httpx.MockTransport(handler),
        metrics=metrics,
    ) as client:
        runtime = _install_integrated_runtime(
            monkeypatch, client, metrics, cached_snapshot
        )
        metadata_before = deepcopy(runtime["record"].cache_metadata)
        raw_before = deepcopy(runtime["record"].raw_features)

        response = await server.call_tool(
            "execute_feature",
            {
                "operation": "update_feature",
                "request": {
                    **TARGET,
                    "featureId": "extrude-safe",
                    "updates": [
                        {"parameterId": "depth", "expression": "27.6 mm"}
                    ],
                },
            },
        )

    payload = json.loads(response[0].text)
    perf_snapshot = metrics.snapshot()
    mutation_protocol_snapshot = runtime["protocol_metrics"].snapshot()
    await _call_compact_verification_read(monkeypatch, features_doc=cached_snapshot)
    protocol_snapshot = runtime["protocol_metrics"].snapshot()
    assert methods == ["GET"]
    assert perf_snapshot["http"]["calls"] == 1
    assert perf_snapshot["http"]["mutation_calls"] == 0
    assert perf_snapshot["tool_invocations"] == 1
    assert perf_snapshot["response_payloads_by_family"]["mutation"]["count"] == 1
    assert mutation_protocol_snapshot["mcp_tool_invocations"] == 1
    assert mutation_protocol_snapshot["model_tool_round_trip_proxy"] == 1
    assert mutation_protocol_snapshot["logical_operations"] == 1
    assert protocol_snapshot["verification_followup_proxy"] == 0
    assert protocol_snapshot["logical_operations"] == 1
    assert runtime["record"].raw_features == raw_before
    assert runtime["record"].cache_metadata == metadata_before
    assert runtime["record"].feature_index is runtime["index"]
    assert runtime["topology_key"] in runtime["entities"]._bodydetails_cache
    assert runtime["topology_key"] in runtime["entities"]._face_frames_cache
    assert payload["mutation"]["state"] == "no_effect"
    assert payload["mutation"]["reason_code"] == "REQUESTED_STATE_ALREADY_PRESENT"
    assert payload["followup"] == {
        "required": False,
        "reason": "requested_state_already_satisfied",
    }
    assert payload["invalidated_state_categories"] == []
    assert payload["invalidation"]["basis"] == "actual"
    assert payload["invalidation"]["feature_cache"] == "unchanged"
    assert payload["invalidation"]["topology_cache"] == "unchanged"
    assert payload["invalidation"]["topology_snapshot_evicted"] is False


@pytest.mark.asyncio
async def test_execute_feature_transmitted_no_effect_stays_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_snapshot = _features_doc(
        depth_expression="10 mm", depth_value=0.01
    )
    reads = [
        _targeted_feature_doc(cached_snapshot),
        _targeted_feature_doc(
            _features_doc(
                depth_expression="10 mm",
                depth_value=0.01,
                source_microversion="post-write-microversion",
            )
        ),
    ]
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, request=request, json=reads.pop(0))
        assert request.method == "POST"
        return httpx.Response(
            200,
            request=request,
            json={
                "feature": {"featureId": "extrude-safe", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            },
        )

    metrics = ExecutionMetrics()
    credentials = OnshapeCredentials(access_key="test", secret_key="test")
    async with OnshapeClient(
        credentials,
        transport=httpx.MockTransport(handler),
        metrics=metrics,
    ) as client:
        runtime = _install_integrated_runtime(
            monkeypatch, client, metrics, cached_snapshot
        )
        response = await server.call_tool(
            "execute_feature",
            {
                "operation": "update_feature",
                "request": {
                    **TARGET,
                    "featureId": "extrude-safe",
                    "updates": [{"parameterId": "depth", "expression": "15 mm"}],
                },
            },
        )

    payload = json.loads(response[0].text)
    mutation_perf_snapshot = metrics.snapshot()
    mutation_protocol_snapshot = runtime["protocol_metrics"].snapshot()
    await _call_compact_verification_read(monkeypatch, features_doc=cached_snapshot)
    protocol_snapshot = runtime["protocol_metrics"].snapshot()
    assert methods == ["GET", "POST", "GET"]
    assert mutation_perf_snapshot["http"]["mutation_calls"] == 1
    assert mutation_protocol_snapshot["mcp_tool_invocations"] == 1
    assert mutation_protocol_snapshot["model_tool_round_trip_proxy"] == 1
    assert mutation_protocol_snapshot["logical_operations"] == 1
    assert protocol_snapshot["verification_followup_proxy"] == 1
    assert protocol_snapshot["logical_operations"] == 1
    assert runtime["record"].cache_metadata["state"] == "partial"
    assert runtime["record"].cache_metadata["topology_state"] == "stale"
    assert runtime["topology_key"] not in runtime["entities"]._bodydetails_cache
    assert payload["mutation"]["state"] == "no_effect"
    assert payload["mutation"]["reason_code"] == "REQUESTED_STATE_UNCHANGED"
    assert payload["followup"]["required"] is True
    assert payload["invalidated_state_categories"] == [
        "feature_tree",
        "bodies",
        "topology",
    ]
    assert payload["invalidation"]["basis"] == "actual"


@pytest.mark.asyncio
async def test_execute_feature_verified_mutation_counts_one_outer_call_and_http_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_snapshot = _features_doc(
        depth_expression="10 mm", depth_value=0.01
    )
    reads = [
        _targeted_feature_doc(cached_snapshot),
        _targeted_feature_doc(
            _features_doc(
                depth_expression="15 mm",
                depth_value=0.015,
                source_microversion="verified-microversion",
            )
        ),
    ]
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, request=request, json=reads.pop(0))
        assert request.method == "POST"
        return httpx.Response(
            200,
            request=request,
            json={
                "feature": {"featureId": "extrude-safe", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            },
        )

    metrics = ExecutionMetrics()
    credentials = OnshapeCredentials(access_key="test", secret_key="test")
    async with OnshapeClient(
        credentials,
        transport=httpx.MockTransport(handler),
        metrics=metrics,
    ) as client:
        runtime = _install_integrated_runtime(
            monkeypatch, client, metrics, cached_snapshot
        )
        response = await server.call_tool(
            "execute_feature",
            {
                "operation": "update_feature",
                "request": {
                    **TARGET,
                    "featureId": "extrude-safe",
                    "updates": [{"parameterId": "depth", "expression": "15 mm"}],
                },
            },
        )

    payload = json.loads(response[0].text)
    perf_snapshot = metrics.snapshot()
    mutation_protocol_snapshot = runtime["protocol_metrics"].snapshot()
    await _call_compact_verification_read(monkeypatch, features_doc=cached_snapshot)
    protocol_snapshot = runtime["protocol_metrics"].snapshot()
    assert methods == ["GET", "POST", "GET"]
    assert perf_snapshot["http"]["calls"] == 3
    assert perf_snapshot["http"]["mutation_calls"] == 1
    assert perf_snapshot["http"]["targeted_reads"] == 2
    assert perf_snapshot["tool_invocations"] == 1
    assert perf_snapshot["approx_model_tool_round_trips"] == 1
    assert perf_snapshot["response_payloads_by_family"]["mutation"]["count"] == 1
    assert mutation_protocol_snapshot["mcp_tool_invocations"] == 1
    assert mutation_protocol_snapshot["model_tool_round_trip_proxy"] == 1
    assert mutation_protocol_snapshot["logical_operations"] == 1
    assert protocol_snapshot["verification_followup_proxy"] == 1
    assert protocol_snapshot["logical_operations"] == 1
    assert payload["mutation"]["state"] == "verified"
    assert payload["followup"] == {
        "required": False,
        "reason": "requested_state_verified",
    }


@pytest.mark.asyncio
async def test_execute_feature_diagnostic_reuses_bounded_public_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_error = "upstream-error-" + ("x" * 40_000)
    long_hint = "recovery-hint-" + ("y" * 20_000)
    result = server.FeatureApplyResult(
        ok=False,
        status="ERROR",
        feature_id="move-safe",
        feature_name="Move body",
        feature_type="transform",
        error_message=long_error,
        transport_ok=True,
        http_ok=True,
        regen_ok=False,
        mutation_verification="failed",
        changed=True,
        verification_scope="feature_state",
        reason_code="FEATURE_REGENERATION_ERROR",
        invalidation={
            "contexts_updated": 1,
            "feature_ids": ["move-safe"],
            "feature_cache": "partially_refreshed",
            "topology_cache": "invalidated",
            "topology_snapshot_evicted": True,
        },
        changes={
            "summary": "faces +500/-500; edges +500/-500",
            "faces_added": [{"id": f"face-new-{index}"} for index in range(500)],
            "faces_removed": [{"id": f"face-old-{index}"} for index in range(500)],
            "edges_added": [{"id": f"edge-new-{index}"} for index in range(500)],
            "edges_removed": [{"id": f"edge-old-{index}"} for index in range(500)],
        },
    )
    delegated = AsyncMock(
        return_value=[
            TextContent(
                type="text",
                text=server._feature_apply_json(
                    result,
                    tool_name="move_body",
                    hints=[long_hint for _ in range(20)],
                ),
            )
        ]
    )
    monkeypatch.setattr(server, "_delegate_feature_operation", delegated)

    response = await server.call_tool(
        "execute_feature",
        {
            "operation": "move_body",
            "request": {
                **TARGET,
                "bodyIds": ["body-safe"],
                "translationX": "5 mm",
                "translationY": "0 mm",
                "translationZ": "0 mm",
            },
            "responseMode": "diagnostic",
        },
    )

    payload = json.loads(response[0].text)
    changes = payload["diagnostic"]["legacy_result"]["changes"]
    legacy = payload["diagnostic"]["legacy_result"]
    assert len(response[0].text.encode("utf-8")) < 10_000
    assert changes["faces_added_count"] == 500
    assert len(changes["faces_added_sample"]) == 8
    assert changes["faces_added_truncated"] is True
    assert changes["truncated"] is True
    assert legacy["response_truncated"] is True
    assert "error_message" in legacy["truncated_fields"]
    assert "hints" in legacy["truncated_fields"]
    assert len(legacy["error_message"]) <= 512
    assert len(legacy["hints"]) == 8
    assert all(len(hint) <= 512 for hint in legacy["hints"])
    assert "face-new-499" not in response[0].text
    assert "edge-old-499" not in response[0].text
    assert long_error not in response[0].text
    assert long_hint not in response[0].text


@pytest.mark.asyncio
async def test_inspect_feature_compact_preserves_full_context_reference_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features_doc = deepcopy(_features_doc())
    features = features_doc["features"]
    assert isinstance(features, list)
    extrude = next(
        feature
        for feature in features
        if isinstance(feature, dict) and feature.get("featureId") == "extrude-safe"
    )
    extrude["parameters"].append(
        {
            "btType": "BTMParameterQueryList-148",
            "parameterId": "entities",
            "queries": [
                {
                    "btType": "BTMIndividualSketchRegionQuery-140",
                    "featureId": "sketch-safe",
                    "deterministicIds": ["region-safe"],
                }
            ],
        }
    )
    get_features = AsyncMock(return_value=features_doc)
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)

    response = await server.call_tool(
        "inspect_feature_compact",
        {**TARGET, "featureId": "extrude-safe", "maxParameters": 4},
    )

    payload = json.loads(response[0].text)
    get_features.assert_awaited_once_with(
        "doc-safe", "workspace-safe", "element-safe"
    )
    assert payload["feature"]["references"] == {
        "confirmed_feature_count": 1,
        "unresolved_reference_count": 0,
        "evidence_types": ["EXPLICIT_QUERY_REFERENCE"],
    }
