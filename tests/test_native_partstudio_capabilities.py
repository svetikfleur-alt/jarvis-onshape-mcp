"""MCP contracts for WP006 Draft and Move Body native subsets."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
from mcp.types import TextContent
import pytest

from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.feature_apply import FeatureApplyResult
from onshape_mcp.api.request_guard import (
    BudgetedAsyncTransport,
    LiveBudgetGuard,
    LiveSuiteBudget,
)
import onshape_mcp.server as server


BASE_IDS = {
    "documentId": "doc-safe",
    "workspaceId": "workspace-safe",
    "elementId": "element-safe",
}


def _decode(result: list[object]) -> dict:
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    return json.loads(result[0].text)


def _apply_result(feature_type: str, feature_id: str) -> FeatureApplyResult:
    return FeatureApplyResult(
        ok=True,
        status="OK",
        feature_id=feature_id,
        feature_name="Native feature",
        feature_type=feature_type,
        transport_ok=True,
        http_ok=True,
        regen_ok=True,
        mutation_verification="verified",
        changed=True,
        verification_scope="requested_feature_fields",
        reason_code="REQUESTED_STATE_VERIFIED",
    )


@pytest.mark.asyncio
async def test_tool_schemas_advertise_only_supported_native_subsets():
    tools = {tool.name: tool for tool in await server.list_tools()}

    draft = tools["create_draft"]
    assert "neutral-plane" in draft.description.lower()
    assert "parting-line" in draft.description.lower()
    assert draft.inputSchema["additionalProperties"] is False
    assert draft.inputSchema["required"] == [
        "documentId",
        "workspaceId",
        "elementId",
        "neutralPlaneId",
        "faceIds",
        "angle",
    ]
    assert set(draft.inputSchema["properties"]) == {
        "documentId",
        "workspaceId",
        "elementId",
        "name",
        "neutralPlaneId",
        "faceIds",
        "angle",
        "reversePullDirection",
        "trackChanges",
    }

    move = tools["move_body"]
    assert "translation-only" in move.description.lower()
    assert "rotation" in move.description.lower()
    assert move.inputSchema["additionalProperties"] is False
    assert move.inputSchema["required"] == [
        "documentId",
        "workspaceId",
        "elementId",
        "bodyIds",
        "translationX",
        "translationY",
        "translationZ",
    ]
    assert set(move.inputSchema["properties"]) == {
        "documentId",
        "workspaceId",
        "elementId",
        "name",
        "bodyIds",
        "translationX",
        "translationY",
        "translationZ",
        "trackChanges",
    }


@pytest.mark.asyncio
async def test_create_draft_dispatches_native_neutral_plane_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply = AsyncMock(return_value=_apply_result("draft", "draft-safe"))
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    response = _decode(
        await server.call_tool(
            "create_draft",
            {
                **BASE_IDS,
                "name": "Release draft",
                "neutralPlaneId": "PLANE-A",
                "faceIds": ["FACE-A", "FACE-B"],
                "angle": "4 deg",
                "reversePullDirection": True,
                "trackChanges": False,
            },
        )
    )

    assert response["ok"] is True
    assert response["tool"] == "create_draft"
    assert response["feature_id"] == "draft-safe"
    assert apply.await_args.args[:4] == (
        server.client,
        "doc-safe",
        "workspace-safe",
        "element-safe",
    )
    assert apply.await_args.kwargs == {"track_changes": False}
    feature = apply.await_args.args[4]["feature"]
    assert feature["featureType"] == "draft"
    parameters = {item["parameterId"]: item for item in feature["parameters"]}
    assert parameters["draftFeatureType"]["value"] == "NEUTRAL_PLANE"
    assert parameters["neutralPlane"]["queries"][0]["deterministicIds"] == ["PLANE-A"]
    assert parameters["draftFaces"]["queries"][0]["deterministicIds"] == [
        "FACE-A",
        "FACE-B",
    ]
    assert parameters["angle"]["expression"] == "4 deg"
    assert parameters["pullDirection"]["value"] is True
    assert parameters["tangentPropagation"]["value"] is False
    assert parameters["reFillet"]["value"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsupported",
    [
        {"draftFeatureType": "PARTING_LINE"},
        {"partingEdges": ["EDGE-A"]},
        {"tangentPropagation": True},
        {"reFillet": True},
    ],
)
async def test_create_draft_rejects_unsupported_variants_before_mutation(
    monkeypatch: pytest.MonkeyPatch, unsupported: dict[str, object]
) -> None:
    apply = AsyncMock()
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    response = _decode(
        await server.call_tool(
            "create_draft",
            {
                **BASE_IDS,
                "neutralPlaneId": "PLANE-A",
                "faceIds": ["FACE-A"],
                "angle": 3,
                **unsupported,
            },
        )
    )

    assert response["status"] == "EXCEPTION"
    assert "only neutral-plane Draft is supported" in response["error_message"]
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_body_dispatches_translation_only_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply = AsyncMock(return_value=_apply_result("transform", "move-safe"))
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    response = _decode(
        await server.call_tool(
            "move_body",
            {
                **BASE_IDS,
                "name": "Shift bracket",
                "bodyIds": ["BODY-A"],
                "translationX": "10 mm",
                "translationY": 0,
                "translationZ": "-0.25 in",
                "trackChanges": True,
            },
        )
    )

    assert response["ok"] is True
    assert response["tool"] == "move_body"
    assert response["feature_id"] == "move-safe"
    assert apply.await_args.kwargs == {"track_changes": True}
    feature = apply.await_args.args[4]["feature"]
    assert feature["featureType"] == "transform"
    parameters = {item["parameterId"]: item for item in feature["parameters"]}
    assert parameters["transformType"]["value"] == "TRANSLATION_3D"
    assert parameters["entities"]["queries"][0]["deterministicIds"] == ["BODY-A"]
    assert parameters["dx"]["expression"] == "10 mm"
    assert parameters["dy"]["expression"] == "0 mm"
    assert parameters["dz"]["expression"] == "-0.25 in"
    assert parameters["makeCopy"]["value"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsupported",
    [
        {"rotation": [0, 0, 90]},
        {"scale": 2},
        {"makeCopy": True},
        {"mateConnectorId": "MC-A"},
        {"directionEntityId": "EDGE-A"},
    ],
)
async def test_move_body_rejects_unsupported_variants_before_mutation(
    monkeypatch: pytest.MonkeyPatch, unsupported: dict[str, object]
) -> None:
    apply = AsyncMock()
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    response = _decode(
        await server.call_tool(
            "move_body",
            {
                **BASE_IDS,
                "bodyIds": ["BODY-A"],
                "translationX": 1,
                "translationY": 0,
                "translationZ": 0,
                **unsupported,
            },
        )
    )

    assert response["status"] == "EXCEPTION"
    assert "only world-coordinate translation is supported" in response["error_message"]
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_body_rejects_zero_motion_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply = AsyncMock()
    monkeypatch.setattr(server, "apply_feature_and_check", apply)

    response = _decode(
        await server.call_tool(
            "move_body",
            {
                **BASE_IDS,
                "bodyIds": ["BODY-A"],
                "translationX": 0,
                "translationY": "0 mm",
                "translationZ": 0,
            },
        )
    )

    assert response["status"] == "EXCEPTION"
    assert "translation must move at least one axis" in response["error_message"]
    apply.assert_not_awaited()


def _canonical_reread_feature(feature_type: str, feature_id: str) -> dict[str, object]:
    if feature_type == "draft":
        parameters = [
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "draftFeatureType",
                "enumName": "DraftFeatureType",
                "value": "NEUTRAL_PLANE",
                "nodeId": "draft-type-node",
            },
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "neutralPlane",
                "queries": [
                    {
                        "btType": "BTMIndividualQuery-138",
                        "deterministicIds": ["PLANE-A"],
                    }
                ],
                "nodeId": "neutral-plane-node",
            },
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "draftFaces",
                "queries": [
                    {
                        "btType": "BTMIndividualQuery-138",
                        "deterministicIds": ["FACE-A"],
                    }
                ],
                "nodeId": "draft-faces-node",
            },
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "angle",
                "expression": "3 deg",
                "value": 0,
                "isInteger": False,
                "nodeId": "draft-angle-node",
            },
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "pullDirection",
                "value": False,
                "nodeId": "pull-direction-node",
            },
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "tangentPropagation",
                "value": False,
                "nodeId": "tangent-node",
            },
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "reFillet",
                "value": False,
                "nodeId": "refillet-node",
            },
        ]
        name = "Draft"
    else:
        parameters = [
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "entities",
                "queries": [
                    {
                        "btType": "BTMIndividualQuery-138",
                        "deterministicIds": ["BODY-A"],
                    }
                ],
                "nodeId": "entities-node",
            },
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "transformType",
                "enumName": "TransformType",
                "value": "TRANSLATION_3D",
                "nodeId": "transform-type-node",
            },
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "dx",
                "expression": "5 mm",
                "value": 0,
                "isInteger": False,
                "nodeId": "dx-node",
            },
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "dy",
                "expression": "0 mm",
                "value": 0,
                "isInteger": False,
                "nodeId": "dy-node",
            },
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "dz",
                "expression": "0 mm",
                "value": 0,
                "isInteger": False,
                "nodeId": "dz-node",
            },
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "makeCopy",
                "value": False,
                "nodeId": "copy-node",
            },
        ]
        name = "Move body"
    return {
        "btType": "BTMFeature-134",
        "featureId": feature_id,
        "featureType": feature_type,
        "name": name,
        "suppressed": False,
        "namespace": "",
        "parameters": parameters,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "feature_type", "feature_id"),
    [
        (
            "create_draft",
            {
                **BASE_IDS,
                "neutralPlaneId": "PLANE-A",
                "faceIds": ["FACE-A"],
                "angle": "3 deg",
                "trackChanges": False,
            },
            "draft",
            "draft-replay",
        ),
        (
            "move_body",
            {
                **BASE_IDS,
                "bodyIds": ["BODY-A"],
                "translationX": "5 mm",
                "translationY": 0,
                "translationZ": 0,
                "trackChanges": False,
            },
            "transform",
            "move-replay",
        ),
    ],
)
async def test_native_mutations_replay_through_guarded_transport_and_reread(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, object],
    feature_type: str,
    feature_id: str,
) -> None:
    requests: list[tuple[str, str]] = []
    posted_payload: dict[str, object] = {}

    async def replay_response(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            posted_payload.update(json.loads(request.content.decode("utf-8")))
            feature = _canonical_reread_feature(feature_type, feature_id)
            return httpx.Response(
                200,
                json={
                    "feature": feature,
                    "featureState": {"featureStatus": "OK"},
                },
            )
        feature = _canonical_reread_feature(feature_type, feature_id)
        return httpx.Response(
            200,
            json={
                "features": [feature],
                "featureStates": {feature_id: {"featureStatus": "OK"}},
            },
        )

    suite = LiveSuiteBudget(limit=2)
    guard = LiveBudgetGuard(f"WP006-{feature_type}", 2, suite)
    client = OnshapeClient(
        OnshapeCredentials(
            access_key="synthetic-access",
            secret_key="synthetic-secret",
            base_url="https://fixture.invalid",
        ),
        transport=BudgetedAsyncTransport(httpx.MockTransport(replay_response), guard),
    )
    monkeypatch.setattr(server, "client", client)

    try:
        response = _decode(await server.call_tool(tool_name, arguments))
    finally:
        await client.close()

    path = "/api/v9/partstudios/d/doc-safe/w/workspace-safe/e/element-safe/features"
    assert requests == [("POST", path), ("GET", path)]
    assert guard.used == 2
    assert suite.used == 2
    assert posted_payload["feature"]["featureType"] == feature_type
    assert response["ok"] is True
    assert response["mutation_verification"] == "verified"
    assert response["reason_code"] == "REQUESTED_STATE_VERIFIED"
    assert response["feature_id"] == feature_id
