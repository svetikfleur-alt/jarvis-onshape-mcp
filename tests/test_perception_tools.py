"""MCP boundary tests for read-only CAD perception tools."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from onshape_mcp import server
from onshape_mcp.api import rendering
from onshape_mcp.api.rendering import RenderedView


TARGET = {
    "documentId": "document-safe",
    "workspaceId": "workspace-safe",
    "elementId": "element-safe",
}


def _payload(contents: list[Any]) -> dict[str, Any]:
    return json.loads(contents[0].text)


def _sketch_document() -> dict[str, object]:
    return {
        "sourceMicroversion": "microversion-sketch-safe",
        "features": [
            {
                "btType": "BTMSketch-151",
                "featureId": "sketch-safe",
                "featureType": "newSketch",
                "name": "Profile",
                "entities": [
                    {
                        "btType": "BTMSketchPoint-279",
                        "entityId": "point-safe",
                    }
                ],
                "constraints": [],
            }
        ],
        "featureStates": {"sketch-safe": {"featureStatus": "OK"}},
    }


def _feature_document() -> dict[str, object]:
    return {
        "sourceMicroversion": "microversion-feature-safe",
        "features": [
            {
                "btType": "BTMFeature-134",
                "featureId": "source-safe",
                "featureType": "newSketch",
                "name": "Source",
                "parameters": [],
            },
            {
                "btType": "BTMFeature-134",
                "featureId": "target-safe",
                "featureType": "extrude",
                "name": "Target",
                "parameters": [
                    {
                        "btType": "BTMParameterFeature-200",
                        "parameterId": "profile",
                        "value": "source-safe",
                    }
                ],
            },
        ],
        "featureStates": {
            "source-safe": {"featureStatus": "OK"},
            "target-safe": {"featureStatus": "OK"},
        },
    }


class _FreshRenderer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def render_part_studio_views(self, **kwargs: Any) -> list[RenderedView]:
        self.requests.append(kwargs)
        rendered = []
        for view in kwargs["views"]:
            content = f"fresh:{view}:{len(self.requests)}".encode()
            image_id = rendering._put_image(
                content,
                {
                    "view": view,
                    "width": kwargs["width"],
                    "height": kwargs["height"],
                },
            )
            rendered.append(
                RenderedView(
                    view=view,
                    image_id=image_id,
                    width=kwargs["width"],
                    height=kwargs["height"],
                    bytes=len(content),
                )
            )
        return rendered


@pytest.mark.asyncio
async def test_perception_tools_register_strict_bounded_schemas() -> None:
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert {
        "inspect_sketch_health",
        "inspect_feature_context",
        "get_visual_snapshot",
    } <= tools.keys()
    for name in (
        "inspect_sketch_health",
        "inspect_feature_context",
        "get_visual_snapshot",
    ):
        assert tools[name].inputSchema["additionalProperties"] is False
    visual = tools["get_visual_snapshot"].inputSchema["properties"]
    assert visual["views"]["maxItems"] == 4
    assert visual["views"]["items"]["enum"] == [
        "iso",
        "front",
        "top",
        "right",
        "back",
        "bottom",
        "left",
    ]
    assert visual["width"]["maximum"] == 1024
    assert visual["height"]["maximum"] == 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "extra"),
    [
        ("inspect_sketch_health", {"sketchFeatureId": "sketch-safe"}),
        ("inspect_feature_context", {"featureId": "target-safe"}),
    ],
)
async def test_perception_reads_reject_empty_targets_before_network(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    extra: dict[str, str],
) -> None:
    get_features = AsyncMock(return_value={})
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)

    result = await server.call_tool(tool_name, {**TARGET, "documentId": "", **extra})

    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["tool"] == tool_name
    assert payload["error"]["type"] == "INVALID_ARGUMENT"
    get_features.assert_not_awaited()


@pytest.mark.asyncio
async def test_inspect_sketch_health_reads_full_tree_and_returns_bounded_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_features = AsyncMock(return_value=_sketch_document())
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)

    result = await server.call_tool(
        "inspect_sketch_health", {**TARGET, "sketchFeatureId": "sketch-safe"}
    )

    payload = _payload(result)
    assert payload["contract"] == "jarvis.sketch_health.v1"
    assert payload["target"] == {
        "document_id": "document-safe",
        "workspace_id": "workspace-safe",
        "element_id": "element-safe",
    }
    assert payload["geometry"]["entity_count"] == 1
    assert payload["constraint_health"]["state"] == "UNKNOWN"
    assert "entities" not in payload
    get_features.assert_awaited_once_with(
        "document-safe", "workspace-safe", "element-safe"
    )


@pytest.mark.asyncio
async def test_inspect_feature_context_uses_one_authoritative_full_tree_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_features = AsyncMock(return_value=_feature_document())
    monkeypatch.setattr(server.partstudio_manager, "get_features", get_features)

    result = await server.call_tool(
        "inspect_feature_context",
        {**TARGET, "featureId": "target-safe", "parameterLimit": 4, "dependencyLimit": 4},
    )

    payload = _payload(result)
    assert payload["contract"] == "jarvis.feature_context.v1"
    assert payload["feature"]["id"] == "target-safe"
    assert payload["upstream"]["feature_ids"] == ["source-safe"]
    assert payload["source_revision"] == "microversion-feature-safe"
    get_features.assert_awaited_once_with(
        "document-safe", "workspace-safe", "element-safe"
    )


@pytest.mark.asyncio
async def test_visual_snapshot_renders_now_and_ignores_unrelated_cached_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rendering, "_IMAGE_CACHE", {})
    monkeypatch.setattr(rendering, "_IMAGE_META", {})
    stale_id = rendering._put_image(b"stale image", {"view": "iso"})
    renderer = _FreshRenderer()
    monkeypatch.setattr(server, "shaded_view_manager", renderer)

    result = await server.call_tool(
        "get_visual_snapshot",
        {**TARGET, "views": ["iso", "front"], "width": 320, "height": 240},
    )

    payload = _payload(result)
    assert payload["contract"] == "jarvis.visual_snapshot.v1"
    assert payload["freshness"] == {
        "rendered_now": True,
        "proof": "rendered_during_this_tool_call",
    }
    assert [view["view"] for view in payload["views"]] == ["iso", "front"]
    assert all(view["image_id"] != stale_id for view in payload["views"])
    assert payload["metadata"]["width"] == 320
    assert payload["metadata"]["height"] == 240
    assert len(result) == 3
    assert [base64.b64decode(item.data) for item in result[1:]] == [
        b"fresh:iso:1",
        b"fresh:front:1",
    ]
    assert renderer.requests == [
        {
            "document_id": "document-safe",
            "workspace_id": "workspace-safe",
            "element_id": "element-safe",
            "views": ["iso", "front"],
            "width": 320,
            "height": 240,
            "edges": True,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"views": ["iso", "front", "top", "right", "back"]},
        {"views": ["iso", "perspective"]},
        {"width": 2048},
        {"height": 0},
    ],
)
async def test_visual_snapshot_rejects_unbounded_requests_before_render(
    monkeypatch: pytest.MonkeyPatch, invalid: dict[str, object]
) -> None:
    renderer = _FreshRenderer()
    monkeypatch.setattr(server, "shaded_view_manager", renderer)

    payload = _payload(await server.call_tool("get_visual_snapshot", {**TARGET, **invalid}))

    assert payload["ok"] is False
    assert payload["error"]["type"] == "INVALID_ARGUMENT"
    assert renderer.requests == []
