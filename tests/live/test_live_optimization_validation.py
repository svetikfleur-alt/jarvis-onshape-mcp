"""Focused disposable validation for V6 performance/reliability paths."""

from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

import pytest

import onshape_mcp.server as server
from onshape_mcp.api.documents import DocumentManager
from onshape_mcp.api.entities import EntityManager
from onshape_mcp.api.partstudio import PartStudioManager
from onshape_mcp.governance import (
    ContextStore,
    DocumentHygieneTracker,
    ExecutionMetrics,
)


def _decode(result: list[Any]) -> dict[str, Any]:
    payload = json.loads(result[0].text)
    assert isinstance(payload, dict)
    return payload


def _verified(payload: dict[str, Any], operation: str) -> dict[str, Any]:
    assert payload.get("ok") is True, f"{operation}: {payload.get('reason_code')}"
    assert payload.get("regen_ok") is True, operation
    assert payload.get("mutation_verification") == "verified", operation
    return payload


def _document_name() -> str:
    now = datetime.now(timezone.utc)
    return f"Jarvis V6 optimization validation {now:%Y%m%dT%H%M%SZ} {uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.live_onshape
@pytest.mark.live_mutation
@pytest.mark.live_optimization_validation
@pytest.mark.live_budget(40)
async def test_live_v6_optimization_validation(
    monkeypatch: pytest.MonkeyPatch,
    live_onshape_client: Any,
    live_budget_guard: Any,
) -> None:
    metrics = ExecutionMetrics()
    live_onshape_client.metrics = metrics
    monkeypatch.setattr(server, "execution_metrics", metrics)
    monkeypatch.setattr(server, "client", live_onshape_client)
    monkeypatch.setattr(
        server, "document_manager", DocumentManager(live_onshape_client)
    )
    monkeypatch.setattr(
        server, "partstudio_manager", PartStudioManager(live_onshape_client)
    )
    monkeypatch.setattr(
        server,
        "entity_manager",
        EntityManager(live_onshape_client, metrics=metrics),
    )
    monkeypatch.setattr(server, "context_store", ContextStore(metrics=metrics))
    monkeypatch.setattr(server, "hygiene_tracker", DocumentHygieneTracker())

    created = _decode(
        await server.call_tool("create_document", {"name": _document_name()})
    )
    assert created.get("ok") is True
    assert created.get("mutation_verification") == "verified"
    ids = {
        "documentId": created["document_id"],
        "workspaceId": created["workspace_id"],
        "elementId": created["part_studio_id"],
    }
    assert all(isinstance(value, str) and value for value in ids.values())

    sketch = _verified(
        _decode(
            await server.call_tool(
                "create_sketch_rectangle",
                {
                    **ids,
                    "name": "V6 base profile",
                    "plane": "Top",
                    "corner1": ["-20 mm", "-15 mm"],
                    "corner2": ["20 mm", "15 mm"],
                },
            )
        ),
        "create_sketch_rectangle",
    )
    extrude = _verified(
        _decode(
            await server.call_tool(
                "create_extrude",
                {
                    **ids,
                    "name": "V6 base extrude",
                    "sketchFeatureId": sketch["feature_id"],
                    "depth": "10 mm",
                    "operationType": "NEW",
                    "trackChanges": False,
                },
            )
        ),
        "create_extrude",
    )

    topology = _decode(
        await server.call_tool(
            "list_entities", {**ids, "kinds": ["faces", "edges"]}
        )
    )
    body = topology["bodies"][0]
    top_faces = [
        face
        for face in body["faces"]
        if face.get("type") == "PLANE"
        and (face.get("outward_axis") or face.get("normal_axis")) == "+Z"
    ]
    assert top_faces
    top_face = max(top_faces, key=lambda face: (face.get("origin") or [0, 0, 0])[2])
    line_edges = [edge for edge in body["edges"] if edge.get("type") == "LINE"]
    assert line_edges
    direction_edge = max(line_edges, key=lambda edge: edge.get("length") or 0.0)

    _verified(
        _decode(
            await server.call_tool(
                "create_linear_pattern",
                {
                    **ids,
                    "name": "V6 feature pattern",
                    "featureIds": [extrude["feature_id"]],
                    "directionEdgeId": direction_edge["id"],
                    "distance": "55 mm",
                    "count": 2,
                    "trackChanges": False,
                },
            )
        ),
        "create_linear_pattern",
    )

    face_sketch = _verified(
        _decode(
            await server.call_tool(
                "create_sketch_circle",
                {
                    **ids,
                    "name": "V6 face reference canary",
                    "faceId": top_face["id"],
                    "center": [0, 0],
                    "radius": "2 mm",
                },
            )
        ),
        "create_sketch_circle",
    )
    assert any(
        "Topology evidence was refreshed" in warning
        for warning in face_sketch.get("warnings", [])
    )

    _verified(
        _decode(
            await server.call_tool(
                "create_shell",
                {
                    **ids,
                    "name": "V6 shell",
                    "faceIds": [top_face["id"]],
                    "thickness": "2 mm",
                    "trackChanges": False,
                },
            )
        ),
        "create_shell",
    )

    post_shell = _decode(
        await server.call_tool("list_entities", {**ids, "kinds": ["edges"]})
    )
    shell_edges = [
        edge
        for candidate_body in post_shell["bodies"]
        for edge in candidate_body.get("edges", [])
        if edge.get("type") == "LINE"
    ]
    assert shell_edges
    chamfer_edge = min(
        shell_edges,
        key=lambda edge: (
            abs((edge.get("length") or 0.0) - 0.01),
            edge.get("id") or "",
        ),
    )
    _verified(
        _decode(
            await server.call_tool(
                "create_chamfer",
                {
                    **ids,
                    "name": "V6 chamfer",
                    "edgeIds": [chamfer_edge["id"]],
                    "distance": "1 mm",
                    "trackChanges": False,
                },
            )
        ),
        "create_chamfer",
    )

    updated = _verified(
        _decode(
            await server.call_tool(
                "update_feature",
                {
                    **ids,
                    "featureId": extrude["feature_id"],
                    "updates": [{"parameterId": "depth", "expression": "12 mm"}],
                },
            )
        ),
        "update_feature",
    )
    assert updated["changed"] is True

    hygiene = _decode(
        await server.call_tool(
            "get_document_hygiene",
            {"documentId": ids["documentId"], "workspaceId": ids["workspaceId"]},
        )
    )
    assert hygiene["status"] == "CLEAN"
    assert hygiene["remaining_unexpected_jarvis_artifacts"] == []

    reported = _decode(await server.call_tool("get_execution_metrics", {}))
    assert reported["tool_invocations"] == 11
    assert reported["approx_model_tool_round_trips"] == 11
    assert reported["http"]["calls"] <= 60
    assert reported["http"]["mutation_calls"] == 8
    assert reported["http"]["broad_reads"] == 0
    assert reported["http"]["feature_list_refreshes"] == 0
    assert reported["http"]["targeted_reads"] >= 8
    assert reported["http"]["topology_reads"] <= 3
    assert reported["semantic_verification_reads"] == 7
    assert live_budget_guard.used == reported["http"]["calls"]
    assert live_budget_guard.blocked == 0

    final_metrics = metrics.snapshot()
    print(
        "LIVE_V6_OPTIMIZATION_RESULT "
        f"document_id={ids['documentId']} "
        f"http_calls={final_metrics['http']['calls']} "
        f"mutation_calls={final_metrics['http']['mutation_calls']} "
        f"tool_invocations={final_metrics['tool_invocations']} "
        f"topology_reads={final_metrics['http']['topology_reads']} "
        f"targeted_reads={final_metrics['http']['targeted_reads']} "
        f"broad_reads={final_metrics['http']['broad_reads']}"
    )
