"""Offline contract coverage for public ``server.call_tool`` handlers.

These tests deliberately exercise the MCP boundary rather than duplicating the
unit tests for builders and API managers.  Every outbound dependency is a mock;
no test in this module constructs a real transport or performs network I/O.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import httpx
from mcp.types import TextContent
import pytest

from onshape_mcp.api.feature_apply import FeatureApplyResult
from onshape_mcp.api.sketch_edit import CascadedRemoval, EditSketchResult
from onshape_mcp.builders.sketch import SketchPlane
from onshape_mcp.governance import ContextStore
import onshape_mcp.server as server


BASE_IDS = {
    "documentId": "doc-safe",
    "workspaceId": "workspace-safe",
    "elementId": "element-safe",
}
PRIVATE_ACCESS_KEY = "AK_PRIVATE_DO_NOT_PRINT"
PRIVATE_SECRET_KEY = "SK_PRIVATE_DO_NOT_PRINT"
PRIVATE_DOCUMENT_ID = "doc-private-do-not-print"
PRIVATE_QUERY_VALUE = "query-private-do-not-print"
PRIVATE_BODY = "body-private-do-not-print"
PRIVATE_HEADER = "header-private-do-not-print"


def _apply_result(
    *,
    feature_id: str = "feature-safe",
    feature_name: str = "Covered feature",
    feature_type: str = "newSketch",
    status: str = "OK",
    ok: bool = True,
    error_message: str | None = None,
) -> FeatureApplyResult:
    return FeatureApplyResult(
        ok=ok,
        status=status,
        feature_id=feature_id,
        feature_name=feature_name,
        feature_type=feature_type,
        error_message=error_message,
        raw={},
    )


def _json_payload(result: list[object]) -> dict:
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    return json.loads(result[0].text)


def _private_http_error(status_code: int = 503) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "POST",
        (
            f"https://{PRIVATE_ACCESS_KEY}:{PRIVATE_SECRET_KEY}@cad.onshape.com"
            f"/api/v9/documents/d/{PRIVATE_DOCUMENT_ID}/w/private/e/private"
            f"?authorization={PRIVATE_QUERY_VALUE}"
        ),
        headers={"Authorization": PRIVATE_HEADER},
        content=PRIVATE_BODY,
    )
    response = httpx.Response(
        status_code,
        request=request,
        headers={"X-Private": PRIVATE_HEADER},
        text=PRIVATE_BODY,
    )
    return httpx.HTTPStatusError(
        f"private diagnostic {PRIVATE_BODY}", request=request, response=response
    )


def _assert_no_private_diagnostics(text: str) -> None:
    for poison in (
        PRIVATE_ACCESS_KEY,
        PRIVATE_SECRET_KEY,
        PRIVATE_DOCUMENT_ID,
        PRIVATE_QUERY_VALUE,
        PRIVATE_BODY,
        PRIVATE_HEADER,
        "authorization=",
    ):
        assert poison not in text


def _model_context_features() -> dict:
    """Small, dependency-bearing feature document for public context tools."""
    return {
        "sourceMicroversion": "microversion-safe",
        "features": [
            {
                "btType": "BTMSketch-151",
                "featureId": "sketch-safe",
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
                        "btType": "BTMParameterUnknown-999",
                        "parameterId": "unknown",
                        "payload": {"raw-private-marker": PRIVATE_BODY},
                    },
                ],
            },
            {
                "btType": "BTMFeature-134",
                "featureId": "extrude-safe",
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
                                "featureId": "sketch-safe",
                                "deterministicIds": ["region-safe"],
                            }
                        ],
                    }
                ],
            },
            {
                "btType": "BTMFeature-134",
                "featureId": "fillet-safe",
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
                                "deterministicIds": ["edge-safe"],
                            }
                        ],
                    }
                ],
            },
        ],
        "featureStates": {
            "sketch-safe": {"featureStatus": "OK"},
            "extrude-safe": {"featureStatus": "OK"},
            "fillet-safe": {"featureStatus": "WARNING"},
        },
    }


async def _start_model_context(monkeypatch: pytest.MonkeyPatch) -> tuple[str, AsyncMock]:
    store = ContextStore()
    get_features = AsyncMock(return_value=_model_context_features())
    monkeypatch.setattr(server, "context_store", store)
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)
    payload = _json_payload(await server.call_tool("start_model_context", BASE_IDS))
    return payload["context_handle"], get_features


@pytest.mark.asyncio
async def test_create_sketch_dispatches_all_entity_shapes_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composite tool must preserve every documented entity path."""
    resolve = AsyncMock(
        return_value=(
            "face-safe",
            SketchPlane.FRONT,
            ["Both `plane` and `faceId` provided; using `faceId`."],
        )
    )
    apply = AsyncMock(return_value=_apply_result())
    monkeypatch.setattr(server, "_resolve_sketch_plane_id", resolve)
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    arguments = {
        **BASE_IDS,
        "name": "Composite sketch",
        "plane": "Top",
        "faceId": "face-safe",
        "entities": [
            {
                "type": "rectangle",
                "corner1": [0, 0],
                "corner2": [2, 3],
                "variableWidth": "widthVar",
                "variableHeight": "heightVar",
            },
            {
                "type": "rounded_rectangle",
                "corner1": [3, 0],
                "corner2": [5, 2],
                "cornerRadius": 0.2,
            },
            {
                "type": "circle",
                "center": [7, 1],
                "radius": 0.5,
                "variableRadius": "radiusVar",
                "variableCenter": ["centerX", "centerY"],
            },
            {"type": "line", "start": [9, 0], "end": [10, 1]},
            {
                "type": "arc",
                "center": [12, 1],
                "radius": 1,
                "startAngle": 15,
                "endAngle": 120,
                "variableCenter": ["invalid-single-value"],
            },
            {
                "id": "explicit-line",
                "type": "line",
                "start": [14, 0],
                "end": [15, 0],
            },
        ],
        "constraints": [{"type": "HORIZONTAL", "entity": "explicit-line"}],
    }

    payload = _json_payload(await server.call_tool("create_sketch", arguments))

    assert payload["ok"] is True
    assert payload["tool"] == "create_sketch"
    assert payload["feature_id"] == "feature-safe"
    assert payload["warnings"] == ["Both `plane` and `faceId` provided; using `faceId`."]
    feature = apply.await_args.args[4]["feature"]
    entity_ids = {entity["entityId"] for entity in feature["entities"]}
    assert "explicit-line" in entity_ids
    assert any(
        constraint.get("constraintType") == "HORIZONTAL" for constraint in feature["constraints"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entities", "constraints", "message"),
    [
        ([], [], "non-empty list"),
        (["not-an-object"], [], "entities[0] must be an object"),
        ([{"type": "spline"}], [], "rectangle | rounded_rectangle"),
        (
            [{"type": "line", "start": [0, 0], "end": [1, 0]}],
            ["bad"],
            "constraints[0] must be an object",
        ),
    ],
)
async def test_create_sketch_validation_is_structured_and_short_circuits_send(
    monkeypatch: pytest.MonkeyPatch,
    entities: list[object],
    constraints: list[object],
    message: str,
) -> None:
    monkeypatch.setattr(
        server,
        "_resolve_sketch_plane_id",
        AsyncMock(return_value=("plane-safe", SketchPlane.FRONT, [])),
    )
    apply = AsyncMock()
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    result = await server.call_tool(
        "create_sketch",
        {**BASE_IDS, "entities": entities, "constraints": constraints},
    )

    payload = _json_payload(result)
    assert payload["ok"] is False
    assert payload["status"] == "EXCEPTION"
    assert payload["tool"] == "create_sketch"
    assert message in payload["error_message"]
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_rounded_rectangle_public_handler_returns_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "_resolve_sketch_plane_id",
        AsyncMock(return_value=("face-safe", SketchPlane.FRONT, [])),
    )
    apply = AsyncMock(
        return_value=_apply_result(
            feature_id="rounded-safe", feature_name="Rounded", feature_type="newSketch"
        )
    )
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    payload = _json_payload(
        await server.call_tool(
            "create_rounded_rectangle_sketch",
            {
                **BASE_IDS,
                "name": "Rounded",
                "corner1": [0, 0],
                "corner2": [20, 10],
                "cornerRadius": 2,
            },
        )
    )

    assert payload["ok"] is True
    assert payload["tool"] == "create_rounded_rectangle_sketch"
    assert payload["feature_id"] == "rounded-safe"
    feature = apply.await_args.args[4]["feature"]
    assert len(feature["entities"]) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        "create_sketch_rectangle",
        "create_rounded_rectangle_sketch",
        "create_sketch_circle",
        "create_sketch_line",
        "create_sketch_arc",
        "create_sketch",
    ],
)
async def test_sketch_mutation_http_errors_are_structured_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    monkeypatch.setattr(
        server,
        "_resolve_sketch_plane_id",
        AsyncMock(return_value=("plane-safe", SketchPlane.FRONT, [])),
    )
    monkeypatch.setattr(
        server,
        "apply_feature_and_check",
        AsyncMock(side_effect=_private_http_error(429)),
    )
    arguments_by_tool = {
        "create_sketch_rectangle": {
            **BASE_IDS,
            "corner1": [0, 0],
            "corner2": [1, 1],
        },
        "create_rounded_rectangle_sketch": {
            **BASE_IDS,
            "corner1": [0, 0],
            "corner2": [2, 2],
            "cornerRadius": 0.2,
        },
        "create_sketch_circle": {**BASE_IDS, "radius": 1},
        "create_sketch_line": {
            **BASE_IDS,
            "startPoint": [0, 0],
            "endPoint": [1, 1],
        },
        "create_sketch_arc": {**BASE_IDS, "radius": 1},
        "create_sketch": {
            **BASE_IDS,
            "entities": [{"type": "line", "start": [0, 0], "end": [1, 1]}],
        },
    }

    text = (await server.call_tool(tool_name, arguments_by_tool[tool_name]))[0].text
    payload = json.loads(text)
    assert payload["ok"] is False
    assert payload["status"] == "EXCEPTION"
    assert payload["tool"] == tool_name
    assert "HTTPStatusError" in payload["error_message"]
    assert "429" in payload["error_message"]
    _assert_no_private_diagnostics(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "missing_field", "enum_field"),
    [
        (
            "create_chamfer",
            {**BASE_IDS, "distance": 1, "chamferType": "EQUAL_OFFSETS"},
            "edgeIds",
            "chamferType",
        ),
        (
            "create_revolve",
            {**BASE_IDS, "operationType": "NEW"},
            "sketchFeatureId",
            "operationType",
        ),
        (
            "create_boolean",
            {**BASE_IDS, "booleanType": "UNION"},
            "toolBodyIds",
            "booleanType",
        ),
    ],
)
async def test_valid_enum_does_not_mask_a_different_missing_required_field(
    tool_name: str,
    arguments: dict[str, object],
    missing_field: str,
    enum_field: str,
) -> None:
    """A broad ``except KeyError`` must not misdiagnose unrelated input gaps."""
    payload = _json_payload(await server.call_tool(tool_name, arguments))

    assert payload["ok"] is False
    assert payload["status"] == "EXCEPTION"
    assert missing_field in payload["error_message"]
    assert f"Invalid {enum_field}" not in payload["error_message"]


@pytest.mark.asyncio
async def test_edit_sketch_returns_structured_change_bookkeeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from onshape_mcp.api import sketch_edit

    edit = AsyncMock(
        return_value=EditSketchResult(
            apply=_apply_result(feature_id="sketch-safe", feature_name="Edited", status="WARNING"),
            added_entity_ids=["line-added"],
            added_constraint_ids=["horizontal-added"],
            removed_entity_ids=["line-removed"],
            removed_constraint_ids=["coincident-removed"],
            cascaded_removals=[
                CascadedRemoval(constraint_id="cascade-safe", referenced="line-removed")
            ],
        )
    )
    monkeypatch.setattr(sketch_edit, "edit_sketch", edit)
    arguments = {
        **BASE_IDS,
        "sketchFeatureId": "sketch-safe",
        "addEntities": [
            {
                "id": "line-added",
                "type": "line",
                "start": [0, 0],
                "end": [1, 0],
            }
        ],
        "addConstraints": [
            {"id": "horizontal-added", "type": "HORIZONTAL", "entity": "line-added"}
        ],
        "removeIds": ["line-removed"],
    }

    payload = _json_payload(await server.call_tool("edit_sketch", arguments))

    assert payload["ok"] is True
    assert payload["status"] == "WARNING"
    assert payload["tool"] == "edit_sketch"
    assert payload["added_entity_ids"] == ["line-added"]
    assert payload["removed_constraint_ids"] == ["coincident-removed"]
    assert payload["cascaded_removals"] == [
        {"constraint_id": "cascade-safe", "referenced": "line-removed"}
    ]
    assert payload["hints"]
    edit.assert_awaited_once_with(
        server.client,
        "doc-safe",
        "workspace-safe",
        "element-safe",
        "sketch-safe",
        add_entities=arguments["addEntities"],
        add_constraints=arguments["addConstraints"],
        remove_ids=arguments["removeIds"],
    )


@pytest.mark.asyncio
async def test_edit_sketch_http_error_is_structured_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from onshape_mcp.api import sketch_edit

    monkeypatch.setattr(
        sketch_edit,
        "edit_sketch",
        AsyncMock(side_effect=_private_http_error(409)),
    )

    text = (await server.call_tool("edit_sketch", {**BASE_IDS, "sketchFeatureId": "sketch-safe"}))[
        0
    ].text

    payload = json.loads(text)
    assert payload["status"] == "EXCEPTION"
    assert payload["tool"] == "edit_sketch"
    assert "409" in payload["error_message"]
    _assert_no_private_diagnostics(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_raw", [False, True])
async def test_inspect_sketch_returns_bounded_summary_and_opt_in_raw(
    monkeypatch: pytest.MonkeyPatch, include_raw: bool
) -> None:
    from onshape_mcp.api import sketch_inspect

    features_doc = {"features": [{"featureId": "sketch-safe"}]}
    get_features = AsyncMock(return_value=features_doc)
    inspect = Mock(
        return_value={
            "name": "Sketch safe",
            "feature_id": "sketch-safe",
            "status": "OK",
            "plane_query": {"deterministicIds": ["JCC"]},
            "entities": [{"id": "line-safe", "type": "line"}],
            "constraints": [{"id": "horizontal-safe", "type": "HORIZONTAL"}],
            "text": "Sketch safe: 1 entity, 1 constraint",
        }
    )
    find = Mock(return_value={"featureId": "sketch-safe", "raw": "opt-in"})
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)
    monkeypatch.setattr(server, "_inspect_sketch_feature", inspect)
    monkeypatch.setattr(sketch_inspect, "find_sketch", find)

    payload = _json_payload(
        await server.call_tool(
            "inspect_sketch",
            {
                **BASE_IDS,
                "sketchName": "Sketch safe",
                "includeRaw": include_raw,
            },
        )
    )

    assert payload["ok"] is True
    assert payload["tool"] == "inspect_sketch"
    assert payload["sketch"]["feature_id"] == "sketch-safe"
    assert payload["sketch"]["entities"] == [{"id": "line-safe", "type": "line"}]
    assert ("raw" in payload) is include_raw
    if include_raw:
        assert payload["raw"]["raw"] == "opt-in"
        find.assert_called_once_with(
            features_doc, sketch_feature_id=None, sketch_name="Sketch safe"
        )
    else:
        find.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sketches",
    [
        [],
        [
            {
                "status": "OK",
                "name": "Sketch safe",
                "entity_count": 2,
                "constraint_count": 1,
                "feature_id": "sketch-safe",
            }
        ],
    ],
)
async def test_list_sketches_returns_structured_empty_and_nonempty_results(
    monkeypatch: pytest.MonkeyPatch, sketches: list[dict[str, object]]
) -> None:
    get_features = AsyncMock(return_value={"features": []})
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)
    monkeypatch.setattr(server, "_list_sketches", Mock(return_value=sketches))

    payload = _json_payload(await server.call_tool("list_sketches", BASE_IDS))

    assert payload["ok"] is True
    assert payload["tool"] == "list_sketches"
    assert payload["sketches"] == sketches
    if sketches:
        assert "Sketch safe" in payload["text"]
        assert "entities=  2" in payload["text"]
    else:
        assert payload["text"] == "SKETCHES: none"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["inspect_sketch", "list_sketches"])
async def test_sketch_read_http_errors_are_structured_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    monkeypatch.setattr(
        server.partstudio_manager,
        "get_features",
        AsyncMock(side_effect=_private_http_error(403)),
    )

    text = (await server.call_tool(tool_name, BASE_IDS))[0].text

    payload = json.loads(text)
    assert payload["status"] == "EXCEPTION"
    assert payload["tool"] == tool_name
    assert "403" in payload["error_message"]
    _assert_no_private_diagnostics(text)


@pytest.mark.asyncio
async def test_create_shell_public_wrapper_preserves_options_and_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply = AsyncMock(return_value=_apply_result(feature_id="shell-safe", feature_type="shell"))
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    payload = _json_payload(
        await server.call_tool(
            "create_shell",
            {
                **BASE_IDS,
                "name": "Shell safe",
                "thickness": 2,
                "faceIds": ["face-a", "face-b"],
                "outward": True,
                "variableThickness": "wallThickness",
                "trackChanges": False,
            },
        )
    )

    assert payload["ok"] is True
    assert payload["tool"] == "create_shell"
    assert payload["feature_id"] == "shell-safe"
    assert apply.await_args.kwargs == {"track_changes": False}
    feature = apply.await_args.args[4]["feature"]
    assert feature["name"] == "Shell safe"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference", "expected_reference"),
    [({"plane": "Top"}, "plane-safe"), ({"referenceFaceId": "face-safe"}, "face-safe")],
)
async def test_create_offset_plane_supports_datum_and_face_references(
    monkeypatch: pytest.MonkeyPatch,
    reference: dict[str, str],
    expected_reference: str,
) -> None:
    get_plane = AsyncMock(return_value="plane-safe")
    apply = AsyncMock(
        return_value=_apply_result(feature_id="plane-feature-safe", feature_type="cPlane")
    )
    monkeypatch.setattr(server.partstudio_manager, "get_plane_id", get_plane)
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    payload = _json_payload(
        await server.call_tool(
            "create_offset_plane",
            {
                **BASE_IDS,
                **reference,
                "offset": 5,
                "variableOffset": "planeOffset",
                "flip": True,
            },
        )
    )

    assert payload["ok"] is True
    assert payload["tool"] == "create_offset_plane"
    assert apply.await_args.kwargs == {"track_changes": False}
    feature = apply.await_args.args[4]["feature"]
    query = next(
        parameter
        for parameter in feature["parameters"]
        if parameter.get("parameterId") == "entities"
    )
    assert expected_reference in json.dumps(query)
    if "plane" in reference:
        get_plane.assert_awaited_once_with("doc-safe", "workspace-safe", "element-safe", "Top")
    else:
        get_plane.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [{}, {"plane": "Front", "referenceFaceId": "face-safe"}],
)
async def test_create_offset_plane_requires_exactly_one_reference(
    monkeypatch: pytest.MonkeyPatch, reference: dict[str, str]
) -> None:
    apply = AsyncMock()
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    payload = _json_payload(
        await server.call_tool("create_offset_plane", {**BASE_IDS, **reference, "offset": 5})
    )

    assert payload["status"] == "EXCEPTION"
    assert payload["tool"] == "create_offset_plane"
    assert "exactly one" in payload["error_message"]
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_context_public_lifecycle_reuses_one_sanitized_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All cached read handlers compose through one public context handle."""
    handle, get_features = await _start_model_context(monkeypatch)

    tree = _json_payload(
        await server.call_tool(
            "get_feature_tree_compact",
            {
                "contextHandle": handle,
                "offset": -4,
                "limit": 500,
                "query": "safe",
                "status": "OK",
            },
        )
    )
    found = _json_payload(
        await server.call_tool(
            "find_features",
            {
                "contextHandle": handle,
                "query": "main",
                "featureType": "extrude",
                "status": "OK",
                "suppressed": False,
                "fromOrdinal": "1",
                "toOrdinal": "9",
                "limit": 500,
            },
        )
    )
    inspected = _json_payload(
        await server.call_tool(
            "inspect_feature",
            {
                "contextHandle": handle,
                "featureId": "extrude-safe",
                "includeParameters": False,
                "maxParameters": 500,
            },
        )
    )
    updated = _json_payload(
        await server.call_tool(
            "update_working_state",
            {
                "contextHandle": handle,
                "phase": "investigate",
                "focusFeatureIds": ["extrude-safe"],
                "hypotheses": [
                    {
                        "hypothesisId": "hypothesis-safe",
                        "claim": "The extrude consumes the profile",
                        "status": "open",
                        "evidenceRefs": ["observation-safe"],
                    }
                ],
                "evidenceRefs": ["observation-safe"],
                "nextAction": {
                    "capability": "inspect_feature_dependencies",
                    "targets": ["extrude-safe"],
                },
            },
        )
    )
    dependencies = _json_payload(
        await server.call_tool(
            "inspect_feature_dependencies",
            {"contextHandle": handle, "featureId": "fillet-safe", "limit": 500},
        )
    )
    dependency_slice = _json_payload(
        await server.call_tool(
            "get_dependency_slice",
            {
                "contextHandle": handle,
                "featureId": "extrude-safe",
                "direction": "both",
                "depth": 99,
                "maxNodes": 999,
                "maxEdges": 999,
            },
        )
    )
    status = _json_payload(await server.call_tool("get_context_status", {"contextHandle": handle}))

    assert tree["level"] == "L1"
    assert tree["data"]["offset"] == 0
    assert found["data"]["rows"][0]["featureId"] == "extrude-safe"
    assert inspected["level"] == "L3"
    assert inspected["data"]["feature"]["parameters"] == []
    assert updated["data"]["meaningful_update"] is True
    assert dependencies["level"] == "L3"
    assert dependency_slice["data"]["limits"] == {
        "depth": 3,
        "maxNodes": 80,
        "maxEdges": 160,
    }
    assert status["data"]["phase"] == "investigate"
    assert status["data"]["focus"] == ["extrude-safe"]
    assert status["data"]["next_action"]["capability"] == ("inspect_feature_dependencies")
    assert status["data"]["ledger"] == {"onshape_reads": 1, "cache_reads": 5}
    assert get_features.await_count == 1
    for payload in (
        tree,
        found,
        inspected,
        updated,
        dependencies,
        dependency_slice,
        status,
    ):
        text = json.dumps(payload)
        assert "raw-private-marker" not in text
        _assert_no_private_diagnostics(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_level"),
    [
        ("get_feature_tree_compact", {}, None),
        ("find_features", {}, "L1"),
        ("inspect_feature", {"featureId": "feature-safe"}, "L3"),
        (
            "inspect_feature_dependencies",
            {"featureId": "feature-safe"},
            "L3",
        ),
        ("get_dependency_slice", {"featureId": "feature-safe"}, "L2"),
        ("update_working_state", {}, "L0"),
        ("get_context_status", {}, None),
    ],
)
async def test_model_context_handlers_return_bounded_missing_handle_errors(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, object],
    expected_level: str | None,
) -> None:
    monkeypatch.setattr(server, "context_store", ContextStore())

    payload = _json_payload(
        await server.call_tool(tool_name, {"contextHandle": "missing-context-safe", **arguments})
    )

    assert payload["error"]["message"] == "Unknown context handle"
    if expected_level is not None:
        assert payload["context_handle"] == "missing-context-safe"
        assert payload["level"] == expected_level
    else:
        assert payload["error"]["context_handle"] == "missing-context-safe"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_feature_tree_compact", {"offset": "not-an-integer"}),
        ("find_features", {"limit": "not-an-integer"}),
        ("inspect_feature", {"featureId": "extrude-safe", "maxParameters": "bad"}),
        (
            "inspect_feature_dependencies",
            {"featureId": "extrude-safe", "limit": "bad"},
        ),
        ("get_dependency_slice", {"featureId": "extrude-safe", "depth": "bad"}),
    ],
)
async def test_model_context_invalid_arguments_return_typed_envelopes(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    handle, _ = await _start_model_context(monkeypatch)

    payload = _json_payload(
        await server.call_tool(tool_name, {"contextHandle": handle, **arguments})
    )

    assert payload["error"]["type"] in {"INVALID_ARGUMENT", "invalid_paging"}
    assert PRIVATE_BODY not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["inspect_feature", "inspect_feature_dependencies", "get_dependency_slice"],
)
async def test_model_context_missing_features_return_typed_errors(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    handle, _ = await _start_model_context(monkeypatch)

    payload = _json_payload(
        await server.call_tool(
            tool_name,
            {"contextHandle": handle, "featureId": "missing-feature-safe"},
        )
    )

    assert payload["error"] == {
        "type": "FEATURE_NOT_FOUND",
        "message": "Feature not found in cached model context",
        "feature_id": "missing-feature-safe",
    }


@pytest.mark.asyncio
async def test_update_working_state_rejects_forbidden_and_invalid_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle, _ = await _start_model_context(monkeypatch)

    forbidden = _json_payload(
        await server.call_tool(
            "update_working_state",
            {"contextHandle": handle, "budget": {"onshape_reads_used": 0}},
        )
    )
    invalid = _json_payload(
        await server.call_tool(
            "update_working_state",
            {
                "contextHandle": handle,
                "focusFeatureIds": [f"feature-{index}" for index in range(13)],
            },
        )
    )

    assert forbidden["error"]["type"] == "WORKING_STATE_FIELD_FORBIDDEN"
    assert forbidden["error"]["fields"] == ["budget"]
    assert invalid["error"]["type"] == "WORKING_STATE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (_private_http_error(502), "onshape_read_failed"),
        (RuntimeError("ordinary offline failure"), "context_start_failed"),
    ],
)
async def test_start_model_context_failures_delete_partial_context_and_stay_safe(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_type: str,
) -> None:
    store = ContextStore()
    delete = Mock(wraps=store.delete)
    monkeypatch.setattr(store, "delete", delete)
    monkeypatch.setattr(server, "context_store", store)
    monkeypatch.setattr(
        server.partstudio_manager,
        "get_features",
        AsyncMock(side_effect=error),
    )

    text = (await server.call_tool("start_model_context", BASE_IDS))[0].text
    payload = json.loads(text)

    assert payload == {"error": {"type": expected_type, "message": "Unable to start model context"}}
    delete.assert_called_once()
    _assert_no_private_diagnostics(text)
