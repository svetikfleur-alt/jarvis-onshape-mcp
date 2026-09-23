"""Offline behavior coverage for drawing-section working primitives."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from onshape_mcp.api.drawing_section import (
    DrawingModifyResult,
    DrawingSectionManager,
    DrawingViewResult,
)
from onshape_mcp.api.export import TranslationResult


def _client(**overrides):
    defaults = {
        "post": AsyncMock(),
        "get": AsyncMock(),
        "delete": AsyncMock(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_create_and_delete_drawing_use_expected_routes_and_payloads():
    client = _client(post=AsyncMock(return_value={"id": "drawing"}))
    manager = DrawingSectionManager(client)

    assert await manager.create_drawing(
        "doc", "ws", drawing_name="Temporary", part_studio_element_id="part-studio"
    ) == "drawing"
    client.post.assert_awaited_once_with(
        "/api/v9/drawings/d/doc/w/ws/create",
        data={"drawingName": "Temporary", "elementId": "part-studio"},
    )

    await manager.delete_drawing("doc", "ws", "drawing")
    client.delete.assert_awaited_once_with("/api/v9/elements/d/doc/w/ws/e/drawing")


@pytest.mark.asyncio
async def test_create_and_modify_require_response_identifiers():
    client = _client(post=AsyncMock(return_value={}))
    manager = DrawingSectionManager(client)
    with pytest.raises(RuntimeError, match="drawing create returned no element id"):
        await manager.create_drawing(
            "doc", "ws", drawing_name="Temporary", part_studio_element_id="part-studio"
        )
    with pytest.raises(RuntimeError, match="/modify returned no request id"):
        await manager._post_modify(
            "doc", "ws", "drawing", description="Create", json_requests=[]
        )


@pytest.mark.asyncio
async def test_delete_drawing_is_best_effort():
    client = _client(delete=AsyncMock(side_effect=RuntimeError("synthetic cleanup")))
    await DrawingSectionManager(client).delete_drawing("doc", "ws", "drawing")
    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_modify_builds_request():
    client = _client(post=AsyncMock(return_value={"id": "request"}))
    manager = DrawingSectionManager(client)
    requests = [{"messageName": "onshapeCreateViews"}]
    assert await manager._post_modify(
        "doc", "ws", "drawing", description="Create", json_requests=requests
    ) == "request"
    client.post.assert_awaited_once_with(
        "/api/v9/drawings/d/doc/w/ws/e/drawing/modify",
        data={"description": "Create", "jsonRequests": requests},
    )


@pytest.mark.asyncio
async def test_poll_modify_parses_success_and_result_details():
    client = _client(
        get=AsyncMock(
            side_effect=[
                {"requestState": "ACTIVE"},
                {
                    "requestState": "DONE",
                    "outputStatusCode": "200",
                    "output": (
                        '{"status":"OK","results":['
                        '{"logicalId":"logical","viewId":"view","status":"OK"},'
                        '{"status":"Failed","errorDescription":"synthetic"}]}'
                    ),
                },
            ]
        )
    )
    result = await DrawingSectionManager(client)._poll_modify(
        "request", timeout=5, interval=0
    )
    assert result.ok is True
    assert result.output_status_code == 200
    assert result.results == [
        DrawingViewResult("logical", "view", "OK"),
        DrawingViewResult("", "", "Failed", "synthetic"),
    ]
    assert client.get.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    ["not-json", {"status": "Failed", "results": []}, None],
)
async def test_poll_modify_handles_failed_or_invalid_outputs(output):
    client = _client(
        get=AsyncMock(
            return_value={
                "requestState": "FAILED",
                "outputStatusCode": None,
                "output": output,
            }
        )
    )
    result = await DrawingSectionManager(client)._poll_modify("request")
    assert result.ok is False
    assert result.request_state == "FAILED"
    assert result.output_status_code == 0
    assert result.results == []


@pytest.mark.asyncio
async def test_poll_modify_returns_last_state_on_timeout():
    last = {"requestState": "ACTIVE", "progress": 0.5}
    client = _client(get=AsyncMock(return_value=last))
    result = await DrawingSectionManager(client)._poll_modify(
        "request", timeout=0, interval=0
    )
    assert result == DrawingModifyResult(False, "request", "ACTIVE", 0, [], last)


@pytest.mark.asyncio
async def test_add_toplevel_view_validates_and_builds_supported_payload(monkeypatch):
    manager = DrawingSectionManager(_client())
    with pytest.raises(ValueError, match="orientation"):
        await manager.add_toplevel_view(
            "doc", "ws", "drawing",
            part_studio_element_id="part-studio", part_id="part", orientation="diagonal",
        )

    post_modify = AsyncMock(return_value="request")
    poll_modify = AsyncMock(
        return_value=DrawingModifyResult(
            True,
            "request",
            "DONE",
            200,
            [DrawingViewResult("logical", "view", "OK")],
            {},
        )
    )
    monkeypatch.setattr(manager, "_post_modify", post_modify)
    monkeypatch.setattr(manager, "_poll_modify", poll_modify)
    result = await manager.add_toplevel_view(
        "doc",
        "ws",
        "drawing",
        part_studio_element_id="part-studio",
        part_id="part",
        orientation="top",
        position=(2.0, 3.0),
        scale_numerator=2.0,
        scale_denominator=5.0,
    )
    assert result.logical_id == "logical"
    kwargs = post_modify.await_args.kwargs
    assert kwargs["description"] == "Add top view"
    view = kwargs["json_requests"][0]["views"][0]
    assert view == {
        "viewType": "TopLevel",
        "position": {"x": 2.0, "y": 3.0},
        "scale": {"scaleSource": "Custom", "numerator": 2.0, "denumerator": 5.0},
        "orientation": "top",
        "reference": {"elementId": "part-studio", "idTag": "part"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modify_result",
    [
        DrawingModifyResult(False, "request", "PRIVATE", 500, [], {}),
        DrawingModifyResult(True, "request", "DONE", 200, [], {}),
    ],
)
async def test_add_toplevel_view_raises_bounded_failure(modify_result, monkeypatch):
    manager = DrawingSectionManager(_client())
    monkeypatch.setattr(manager, "_post_modify", AsyncMock(return_value="request"))
    monkeypatch.setattr(manager, "_poll_modify", AsyncMock(return_value=modify_result))
    with pytest.raises(RuntimeError) as exc_info:
        await manager.add_toplevel_view(
            "doc", "ws", "drawing", part_studio_element_id="part-studio", part_id="part"
        )
    assert str(exc_info.value) in {
        "TopLevel view creation failed: state=UNKNOWN; output_status_code=500",
        "TopLevel view creation failed: state=DONE; output_status_code=200",
    }


@pytest.mark.asyncio
async def test_translate_drawing_returns_bounded_start_failure():
    start = {"requestState": "FAILED", "diagnostic": "safe"}
    client = _client(post=AsyncMock(return_value=start))
    result = await DrawingSectionManager(client).translate_drawing_to_png(
        "doc", "ws", "drawing", format_name="jpeg"
    )
    assert result == TranslationResult(
        ok=False,
        state="FAILED",
        translation_id="",
        format_name="JPEG",
        error_message=(
            "drawing translation start returned no id; "
            "keys=['requestState', 'diagnostic']"
        ),
        raw=start,
    )


@pytest.mark.asyncio
async def test_translate_drawing_delegates_polling():
    expected = TranslationResult(True, "DONE", "translation", "PNG", data=b"png")
    exporter = SimpleNamespace(wait_for_translation=AsyncMock(return_value=expected))
    client = _client(post=AsyncMock(return_value={"id": "translation"}))
    manager = DrawingSectionManager(client, exporter=exporter)
    result = await manager.translate_drawing_to_png(
        "doc",
        "ws",
        "drawing",
        format_name="png",
        timeout_seconds=7,
        poll_interval_seconds=0.2,
    )
    assert result is expected
    client.post.assert_awaited_once_with(
        "/api/v9/drawings/d/doc/w/ws/e/drawing/translations",
        data={"formatName": "PNG", "storeInDocument": False},
    )
    exporter.wait_for_translation.assert_awaited_once_with(
        "translation",
        source_document_id="doc",
        format_name="PNG",
        timeout_seconds=7,
        poll_interval_seconds=0.2,
    )


@pytest.mark.asyncio
async def test_section_creation_and_rendering_remain_explicitly_unsupported():
    manager = DrawingSectionManager(_client())
    with pytest.raises(NotImplementedError, match="does not support section-view creation"):
        await manager._add_section_view(
            "doc",
            "ws",
            "drawing",
            parent_view_logical_id="parent",
            plane_origin=(0, 0, 0),
            plane_normal=(0, 0, 1),
        )
    with pytest.raises(NotImplementedError, match="render_section is not implementable"):
        await manager.render_section(
            "doc",
            "ws",
            "part-studio",
            plane_origin=(0, 0, 0),
            plane_normal=(0, 0, 1),
        )
