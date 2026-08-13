"""Local-only, read-only Onshape Deep Read acceptance scenario."""

from __future__ import annotations

import json
from typing import Any

import pytest

import onshape_mcp.server as server
from onshape_mcp.api.partstudio import PartStudioManager
from onshape_mcp.governance import (
    MAX_SLICE_DEPTH,
    MAX_SLICE_EDGES,
    MAX_SLICE_NODES,
    ContextStore,
)


MAX_INLINE_BYTES = 32_768
MAX_NORMALIZED_PARAMETER_BYTES = 4_096
SANITIZED_FEATURE_ROUTE = (
    "/api/v9/partstudios/d/{documentId}/w/{workspaceId}/e/{elementId}/features"
)


def _decode(result: list[Any], failure_code: str) -> dict[str, Any]:
    if len(result) != 1:
        pytest.fail(failure_code)
    try:
        payload = json.loads(result[0].text)
    except (AttributeError, TypeError, json.JSONDecodeError):
        pytest.fail(failure_code)
    if not isinstance(payload, dict):
        pytest.fail(failure_code)
    return payload


def _contains_forbidden_raw_data(value: Any) -> bool:
    if isinstance(value, dict):
        keys = set(value)
        if "features" in keys or "sourceMicroversion" in keys:
            return True
        if {"serializationVersion", "featureStates"}.issubset(keys):
            return True
        return any(_contains_forbidden_raw_data(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_raw_data(item) for item in value)
    return False


def _select_parameterized_ok_feature(rows: list[Any]) -> dict[str, Any] | None:
    """Choose an inspectable OK feature without exposing a row in failures."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        parameter_count = row.get("parameterCount")
        feature_id = row.get("featureId")
        if (
            row.get("status") == "OK"
            and isinstance(feature_id, str)
            and bool(feature_id)
            and isinstance(parameter_count, int)
            and not isinstance(parameter_count, bool)
            and parameter_count > 0
        ):
            return row
    return None


def _has_useful_summary(parameter: dict[str, Any]) -> bool:
    for key in ("value_summary", "reference_summary"):
        if key not in parameter:
            continue
        summary = parameter[key]
        useful = bool(summary) if isinstance(summary, (str, list, dict)) else summary is not None
        if not useful:
            continue
        try:
            encoded = json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        except (TypeError, ValueError):
            continue
        if len(encoded) <= MAX_NORMALIZED_PARAMETER_BYTES:
            return True
    return False


def _has_useful_normalized_parameters(feature: dict[str, Any]) -> bool:
    """Validate the bounded normalized projection, never the raw feature payload."""
    parameters = feature.get("parameters")
    parameter_count = feature.get("parameter_count")
    returned = feature.get("returned_parameters")
    if (
        not isinstance(parameters, list)
        or not parameters
        or not isinstance(parameter_count, int)
        or isinstance(parameter_count, bool)
        or not isinstance(returned, int)
        or isinstance(returned, bool)
        or returned != len(parameters)
        or returned <= 0
        or returned > parameter_count
    ):
        return False

    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_id = parameter.get("parameterId")
        parameter_type = parameter.get("parameterType")
        if (
            isinstance(parameter_id, str)
            and 0 < len(parameter_id) <= 128
            and isinstance(parameter_type, str)
            and 0 < len(parameter_type) <= 128
            and _has_useful_summary(parameter)
        ):
            return True
    return False


def _require_bounded_envelopes(envelopes: list[dict[str, Any]]) -> None:
    for envelope in envelopes:
        try:
            encoded = json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            inline_bytes = envelope["cost"]["inline_bytes"]
        except (KeyError, TypeError, ValueError):
            pytest.fail("LIVE_ENVELOPE_INVALID")
        if len(encoded) > MAX_INLINE_BYTES or not isinstance(inline_bytes, int):
            pytest.fail("LIVE_ENVELOPE_CAP_EXCEEDED")
        if inline_bytes > MAX_INLINE_BYTES:
            pytest.fail("LIVE_ENVELOPE_CAP_EXCEEDED")
        if _contains_forbidden_raw_data(envelope):
            pytest.fail("LIVE_RAW_FEATURE_DOCUMENT_EXPOSED")


@pytest.mark.asyncio
@pytest.mark.live_onshape
@pytest.mark.live_readonly
@pytest.mark.live_budget(1)
async def test_live_deep_read_01(
    monkeypatch: pytest.MonkeyPatch,
    live_onshape_client: Any,
    live_model_ids: dict[str, str],
    live_budget_guard: Any,
    live_session_telemetry: list[Any],
) -> None:
    """Exercise one authoritative read and cached public Deep Read operations."""
    store = ContextStore()
    monkeypatch.setattr(server, "partstudio_manager", PartStudioManager(live_onshape_client))
    monkeypatch.setattr(server, "context_store", store)
    context_handle: str | None = None
    envelopes: list[dict[str, Any]] = []

    try:
        started = _decode(
            await server.call_tool(
                "start_model_context",
                {
                    "documentId": live_model_ids["document_id"],
                    "workspaceId": live_model_ids["workspace_id"],
                    "elementId": live_model_ids["element_id"],
                },
            ),
            "LIVE_CONTEXT_START_INVALID",
        )
        envelopes.append(started)
        context_handle = started.get("context_handle")
        if not isinstance(context_handle, str) or not context_handle:
            pytest.fail("LIVE_CONTEXT_HANDLE_MISSING")

        tree = _decode(
            await server.call_tool(
                "get_feature_tree_compact",
                {"contextHandle": context_handle, "offset": 0, "limit": 50},
            ),
            "LIVE_TREE_INVALID",
        )
        envelopes.append(tree)
        tree_rows = tree.get("data", {}).get("rows")
        if not isinstance(tree_rows, list) or not tree_rows:
            pytest.fail("LIVE_TREE_EMPTY")

        found = _decode(
            await server.call_tool(
                "find_features",
                {"contextHandle": context_handle, "status": "OK", "limit": 50},
            ),
            "LIVE_SEARCH_INVALID",
        )
        envelopes.append(found)
        search_rows = found.get("data", {}).get("rows")
        if not isinstance(search_rows, list) or not search_rows:
            pytest.fail("LIVE_SEARCH_EMPTY")
        selected = _select_parameterized_ok_feature(search_rows)
        if selected is None:
            pytest.fail("LIVE_PARAMETERIZED_OK_FEATURE_NOT_FOUND")
        feature_id = selected.get("featureId")
        if not isinstance(feature_id, str) or not feature_id:
            pytest.fail("LIVE_FEATURE_ID_MISSING")

        inspection = _decode(
            await server.call_tool(
                "inspect_feature",
                {
                    "contextHandle": context_handle,
                    "featureId": feature_id,
                    "includeParameters": True,
                    "maxParameters": 80,
                },
            ),
            "LIVE_INSPECTION_INVALID",
        )
        envelopes.append(inspection)
        inspected_feature = inspection.get("data", {}).get("feature")
        if not isinstance(inspected_feature, dict) or not inspected_feature:
            pytest.fail("LIVE_INSPECTION_EMPTY")
        if inspected_feature.get("status") != "OK":
            pytest.fail("LIVE_INSPECTION_STATUS_NOT_OK")
        if not _has_useful_normalized_parameters(inspected_feature):
            pytest.fail("LIVE_NORMALIZED_PARAMETERS_INVALID")
        dependency_slice = _decode(
            await server.call_tool(
                "get_dependency_slice",
                {
                    "contextHandle": context_handle,
                    "featureId": feature_id,
                    "direction": "both",
                    "depth": MAX_SLICE_DEPTH,
                    "maxNodes": MAX_SLICE_NODES,
                    "maxEdges": MAX_SLICE_EDGES,
                },
            ),
            "LIVE_SLICE_INVALID",
        )
        envelopes.append(dependency_slice)
        slice_data = dependency_slice.get("data", {})
        nodes = slice_data.get("nodes")
        edges = slice_data.get("edges")
        limits = slice_data.get("limits")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            pytest.fail("LIVE_SLICE_INVALID")
        node_ids = {
            node.get("featureId")
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("featureId"), str)
        }
        if feature_id not in node_ids:
            pytest.fail("LIVE_SLICE_ROOT_MISSING")
        if len(nodes) > MAX_SLICE_NODES or len(edges) > MAX_SLICE_EDGES:
            pytest.fail("LIVE_SLICE_CAP_EXCEEDED")
        if not isinstance(limits, dict) or limits.get("depth") != MAX_SLICE_DEPTH:
            pytest.fail("LIVE_SLICE_DEPTH_INVALID")
        for edge in edges:
            if not isinstance(edge, dict):
                pytest.fail("LIVE_SLICE_EDGE_INVALID")
            if edge.get("fromFeatureId") not in node_ids:
                pytest.fail("LIVE_SLICE_EDGE_ENDPOINT_INVALID")
            if edge.get("toFeatureId") not in node_ids:
                pytest.fail("LIVE_SLICE_EDGE_ENDPOINT_INVALID")

        updated = _decode(
            await server.call_tool(
                "update_working_state",
                {
                    "contextHandle": context_handle,
                    "focusFeatureIds": [feature_id],
                    "hypotheses": [
                        {
                            "hypothesisId": "live-deep-read-ok-feature",
                            "claim": "The selected normalized feature remains inspectable",
                            "status": "open",
                        }
                    ],
                    "nextAction": {
                        "capability": "inspect_feature",
                        "targets": [feature_id],
                    },
                },
            ),
            "LIVE_WORKING_STATE_INVALID",
        )
        envelopes.append(updated)
        if updated.get("data", {}).get("meaningful_update") is not True:
            pytest.fail("LIVE_WORKING_STATE_NOT_MEANINGFUL")

        repeated = _decode(
            await server.call_tool(
                "inspect_feature",
                {
                    "contextHandle": context_handle,
                    "featureId": feature_id,
                    "includeParameters": True,
                    "maxParameters": 80,
                },
            ),
            "LIVE_REPEAT_INSPECTION_INVALID",
        )
        envelopes.append(repeated)
        if repeated.get("cache", {}).get("cache_repeat") is not True:
            pytest.fail("LIVE_REPEAT_INSPECTION_NOT_CACHED")
        repeated_feature = repeated.get("data", {}).get("feature")
        if not isinstance(repeated_feature, dict):
            pytest.fail("LIVE_REPEAT_INSPECTION_INVALID")
        if repeated_feature.get("featureId") != feature_id:
            pytest.fail("LIVE_REPEAT_FEATURE_MISMATCH")
        if repeated_feature.get("status") != "OK":
            pytest.fail("LIVE_REPEAT_STATUS_NOT_OK")

        status = _decode(
            await server.call_tool(
                "get_context_status", {"contextHandle": context_handle}
            ),
            "LIVE_CONTEXT_STATUS_INVALID",
        )
        envelopes.append(status)

        ledger = status.get("data", {}).get("ledger")
        if ledger != {"onshape_reads": 1, "cache_reads": 5}:
            pytest.fail("LIVE_LEDGER_INVALID")
        if live_budget_guard.used != 1 or live_budget_guard.blocked != 0:
            pytest.fail("LIVE_REQUEST_COUNT_INVALID")

        start_summary = started.get("summary")
        status_data = status.get("data")
        if not isinstance(start_summary, dict) or not isinstance(status_data, dict):
            pytest.fail("LIVE_REVISION_STATE_INVALID")
        if "revision_id" not in start_summary or "revision_id" not in status_data:
            pytest.fail("LIVE_REVISION_STATE_INVALID")
        stored_revision = store.get(context_handle).working_state.revision_id
        start_revision = start_summary["revision_id"]
        status_revision = status_data["revision_id"]
        if isinstance(start_revision, str) and start_revision:
            if stored_revision != start_revision or status_revision != start_revision:
                pytest.fail("LIVE_REVISION_PROPAGATION_INVALID")
        elif not (
            start_revision is None
            and stored_revision is None
            and status_revision is None
        ):
            pytest.fail("LIVE_REVISION_UNKNOWN_INVALID")

        _require_bounded_envelopes(envelopes)

        if len(live_session_telemetry) != 1:
            pytest.fail("LIVE_TELEMETRY_COUNT_INVALID")
        event = live_session_telemetry[0]
        if event.test_identifier != "LIVE-DEEP-READ-01":
            pytest.fail("LIVE_TELEMETRY_SCENARIO_INVALID")
        if event.method != "GET" or event.route != SANITIZED_FEATURE_ROUTE:
            pytest.fail("LIVE_TELEMETRY_REQUEST_INVALID")
        if event.test_used != 1 or event.test_limit != 1:
            pytest.fail("LIVE_TELEMETRY_TEST_BUDGET_INVALID")
        if (
            event.suite_used != live_budget_guard.suite_budget.used
            or event.suite_limit != live_budget_guard.suite_budget.limit
            or event.suite_used > event.suite_limit
        ):
            pytest.fail("LIVE_TELEMETRY_SUITE_BUDGET_INVALID")
        if event.blocked_scope != "none":
            pytest.fail("LIVE_TELEMETRY_BLOCKED_INVALID")
    finally:
        if context_handle is not None:
            store.delete(context_handle)
