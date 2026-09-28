"""Agent-facing execution protocol contracts."""

from __future__ import annotations

import json

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
