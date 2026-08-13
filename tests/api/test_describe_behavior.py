"""Offline behavior tests for the combined Part Studio description."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from onshape_mcp.api.describe import (
    DescribeManager,
    _bbox_text,
    _body_topology_text,
    _extract_fs_vector,
    _feature_tree_text,
    _fmt_mm,
    _fmt_vec_mm,
    _fs_num,
    _mass_props_text,
    _parse_bbox_response,
    _parse_fs_area_map,
    _physical_summary_text,
)
from onshape_mcp.api.rendering import RenderedView


@dataclass
class _AsyncResult:
    value: Any = None
    error: Exception | None = None

    async def call(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        if self.error is not None:
            raise self.error
        return self.value


class _PartStudio:
    def __init__(self, result: _AsyncResult):
        self._result = result

    async def get_features(self, *args: Any) -> Any:
        return await self._result.call(*args)


class _Entities:
    def __init__(self, result: _AsyncResult):
        self._result = result

    async def list_entities(self, *args: Any) -> Any:
        return await self._result.call(*args)


class _FeatureScript:
    def __init__(
        self,
        *,
        bbox: _AsyncResult | None = None,
        areas: _AsyncResult | None = None,
    ):
        self._bbox = bbox or _AsyncResult({})
        self._areas = areas or _AsyncResult({})

    async def get_bounding_box(self, *args: Any) -> Any:
        return await self._bbox.call(*args)

    async def evaluate(self, *args: Any) -> Any:
        return await self._areas.call(*args)


class _Measurements:
    def __init__(self, result: _AsyncResult):
        self._result = result

    async def mass_properties_part_studio(self, *args: Any) -> Any:
        return await self._result.call(*args)


class _Renderer:
    def __init__(self, result: _AsyncResult):
        self._result = result
        self.request: tuple[tuple[Any, ...], dict[str, Any]] | None = None

    async def render_part_studio_views(self, *args: Any, **kwargs: Any) -> Any:
        self.request = (args, kwargs)
        return await self._result.call(*args, **kwargs)


def _fs_vector(x: float, y: float, z: float) -> dict[str, Any]:
    return {
        "value": [
            {"value": x},
            {"value": {"value": y}},
            z,
        ]
    }


def _bbox_response() -> dict[str, Any]:
    return {
        "result": {
            "value": [
                {"key": {"value": "minCorner"}, "value": _fs_vector(0, 0, 0)},
                {
                    "key": {"value": "maxCorner"},
                    "value": _fs_vector(0.02, 0.01, 0.005),
                },
            ]
        }
    }


def _entity_response() -> dict[str, Any]:
    return {
        "bodies": [
            {
                "body_index": 0,
                "body_id": "synthetic-body",
                "body_type": "SOLID",
                "faces": [
                    {
                        "id": "synthetic-plane",
                        "type": "PLANE",
                        "description": "top plane",
                    },
                    {
                        "id": "synthetic-cylinder",
                        "type": "CYLINDER",
                        "description": "outer wall",
                    },
                ],
                "edges": [
                    {"id": "synthetic-short", "type": "LINE", "length": 0.00001},
                    {"id": "synthetic-long", "type": "CIRCLE", "length": 0.02},
                    {"id": "synthetic-zero", "type": "LINE", "length": 0},
                ],
            }
        ]
    }


def _mass_response() -> dict[str, Any]:
    return {
        "bodies": {
            "synthetic-body": {
                "volume": [0, 0.000001],
                "centroid": [0.0, 0.002, 0.0, 0.004, 0.0, 0.006],
            }
        }
    }


def _area_response() -> dict[str, Any]:
    return {
        "result": {
            "value": [
                {
                    "key": {"value": "synthetic-plane"},
                    "value": {"value": 0.0002},
                },
                {
                    "key": {"value": "synthetic-sliver"},
                    "value": {"value": 0.00000001},
                },
            ]
        }
    }


def test_scalar_formatters_render_meters_as_millimeters_and_unknowns():
    assert _fmt_mm(None) == "?"
    assert _fmt_mm(0.00125) == "1.25 mm"
    assert _fmt_vec_mm(None) == "?"
    assert _fmt_vec_mm([0.001, 0.002, 0.003]) == "(1.0, 2.0, 3.0) mm"


def test_feature_tree_reports_status_type_and_suppression():
    text = _feature_tree_text(
        {
            "features": [
                {
                    "featureId": "feature-one",
                    "name": "Base sketch",
                    "featureType": "newSketch",
                    "suppressed": True,
                },
                {"name": "Fallback type", "btType": "BTMFeature-134"},
            ],
            "featureStates": {"feature-one": {"featureStatus": "OK"}},
        }
    )

    assert "FEATURE TREE (2 features)" in text
    assert "[OK   ] Base sketch" in text
    assert "(newSketch) id=feature-one [suppressed]" in text
    assert "[?    ] Fallback type" in text
    assert "(BTMFeature-134) id=" in text
    assert _feature_tree_text({}) == "FEATURE TREE (0 features):"


def test_body_topology_summarizes_face_types_and_empty_models():
    text = _body_topology_text(_entity_response())

    assert "BODIES (1)" in text
    assert "faces=2 (1 cylinder, 1 plane) edges=3" in text
    assert "FACE synthetic-plane: top plane" in text
    assert "FACE synthetic-cylinder: outer wall" in text
    assert _body_topology_text({}) == "BODIES: none"


def test_bbox_text_reports_dimensions_and_missing_shape():
    bbox = {
        "minCorner": {"x": -0.001, "y": 0.0, "z": 0.001},
        "maxCorner": {"x": 0.019, "y": 0.01, "z": 0.006},
    }

    text = _bbox_text(bbox)

    assert "BOUNDING BOX: 20.00 x 10.00 x 5.00 mm" in text
    assert "min=(-1.00, 0.00, 1.00) mm" in text
    assert "max=(19.00, 10.00, 6.00) mm" in text
    assert _bbox_text(None) == "BOUNDING BOX: unknown"
    assert _bbox_text({}) == "BOUNDING BOX: unknown"


def test_physical_summary_reports_ranges_breakdowns_and_suspect_geometry():
    bbox = _parse_bbox_response(_bbox_response())

    text = _physical_summary_text(
        _entity_response(),
        bbox,
        _mass_response(),
        {"synthetic-plane": 0.0002, "synthetic-sliver": 0.00000001},
    )

    assert "bodies: 1   volume: 1000.0 mm^3" in text
    assert "bbox: 20.00 x 10.00 x 5.00 mm" in text
    assert "faces: 2 (1 cylinder, 1 plane)" in text
    assert "edges: 3 (1 circle, 2 line)" in text
    assert "min=0.010 mm^2 (synthetic-sliver)" in text
    assert "max=200.000 mm^2 (synthetic-plane)" in text
    assert "edge lengths: min=0.010 mm  max=20.000 mm" in text
    assert "face synthetic-sliver: tiny face area" in text
    assert "edge synthetic-short: tiny edge length" in text


def test_physical_summary_handles_unavailable_and_malformed_measurements():
    malformed_mass = {"bodies": {"bad": {"volume": object()}}}

    text = _physical_summary_text(
        {
            "bodies": [
                {
                    "faces": [{"type": "PLANE"}],
                    "edges": [{"length": "unknown"}],
                }
            ]
        },
        None,
        malformed_mass,
        {"not-a-number": "unknown"},
    )

    assert "volume: unknown" in text
    assert "bbox: unknown" in text
    assert "faces: 1 (1 plane)" in text
    assert "edges: 1 (1 ?)" in text
    assert "face areas: (FS probe unavailable)" in text
    assert "edge lengths: no measurable edges" in text
    assert "suspect geometry: none" in text


def test_featurescript_area_parser_ignores_malformed_entries():
    response = _area_response()
    response["result"]["value"].extend(
        [
            "not-a-map",
            {"key": {"value": 42}, "value": {"value": 1.0}},
            {"key": {"value": "missing-value"}, "value": {}},
            {"key": {"value": "nested-invalid"}, "value": "bad"},
        ]
    )

    assert _parse_fs_area_map(response) == {
        "synthetic-plane": 0.0002,
        "synthetic-sliver": 0.00000001,
    }
    assert _parse_fs_area_map({"result": {"value": "not-a-list"}}) == {}


def test_mass_properties_text_formats_bodies_and_empty_results():
    text = _mass_props_text(_mass_response())

    assert "MASS PROPERTIES:" in text
    assert "body synthetic-body: volume=1000.0 mm^3" in text
    assert "centroid≈(1.00 mm, ...)" in text
    assert _mass_props_text({}) == "MASS PROPERTIES: none"
    assert "volume=0.0 mm^3" in _mass_props_text(
        {"bodies": {"short": {"volume": [2], "centroid": "unknown"}}}
    )
    assert "centroid" not in _mass_props_text(
        {"bodies": {"bad": {"volume": [0, 1], "centroid": [object(), object(), 0]}}}
    )


def test_fs_vector_and_number_parsers_cover_serialized_variants():
    assert _extract_fs_vector(_fs_vector(1, 2, 3)) == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert _extract_fs_vector(None) is None
    assert _extract_fs_vector({"value": [1, 2]}) is None
    assert _fs_num({"value": {"value": 4}}) == 4.0
    assert _fs_num({"value": 5}) == 5.0
    assert _fs_num(6) == 6.0
    assert _fs_num({"value": "bad"}) == 0.0


@pytest.mark.asyncio
async def test_describe_part_studio_combines_all_successful_reads():
    view = RenderedView("iso", "synthetic-image", 640, 480, 321)
    renderer = _Renderer(_AsyncResult([view]))
    manager = DescribeManager(
        object(),
        entities=_Entities(_AsyncResult(_entity_response())),
        renderer=renderer,
        measurements=_Measurements(_AsyncResult(_mass_response())),
        featurescript=_FeatureScript(
            bbox=_AsyncResult(_bbox_response()),
            areas=_AsyncResult(_area_response()),
        ),
        partstudio=_PartStudio(
            _AsyncResult(
                {
                    "features": [
                        {
                            "featureId": "feature-one",
                            "name": "Base sketch",
                            "featureType": "newSketch",
                        }
                    ],
                    "featureStates": {"feature-one": {"featureStatus": "OK"}},
                }
            )
        ),
    )

    snapshot = await manager.describe_part_studio(
        "synthetic-document",
        "synthetic-workspace",
        "synthetic-element",
        views=["iso"],
        render_width=640,
        render_height=480,
    )

    assert snapshot.views == [view]
    assert "FEATURE TREE (1 features)" in snapshot.structured_text
    assert "PHYSICAL SUMMARY:" in snapshot.structured_text
    assert "VIEWS RENDERED:\n\n  iso: image_id=synthetic-image (640x480, 321B)" in snapshot.structured_text
    assert snapshot.raw["bbox"] == {
        "minCorner": {"x": 0.0, "y": 0.0, "z": 0.0},
        "maxCorner": {"x": 0.02, "y": 0.01, "z": 0.005},
    }
    assert snapshot.raw["face_areas"] == {
        "synthetic-plane": 0.0002,
        "synthetic-sliver": 0.00000001,
    }
    assert renderer.request is not None
    assert renderer.request[1] == {"views": ["iso"], "width": 640, "height": 480}


@pytest.mark.asyncio
async def test_describe_part_studio_degrades_independent_read_failures_to_empty_sections():
    failure = RuntimeError("synthetic read failed")
    manager = DescribeManager(
        object(),
        entities=_Entities(_AsyncResult(error=failure)),
        renderer=_Renderer(_AsyncResult(error=failure)),
        measurements=_Measurements(_AsyncResult(error=failure)),
        featurescript=_FeatureScript(
            bbox=_AsyncResult(error=failure),
            areas=_AsyncResult(error=failure),
        ),
        partstudio=_PartStudio(_AsyncResult(error=failure)),
    )

    snapshot = await manager.describe_part_studio(
        "synthetic-document", "synthetic-workspace", "synthetic-element"
    )

    assert snapshot.views == []
    assert snapshot.raw == {
        "features": {},
        "entities": {"bodies": []},
        "bbox": None,
        "mass_properties": {},
        "face_areas": {},
    }
    assert "FEATURE TREE (0 features)" in snapshot.structured_text
    assert "BODIES: none" in snapshot.structured_text
    assert "BOUNDING BOX: unknown" in snapshot.structured_text
    assert "MASS PROPERTIES: none" in snapshot.structured_text


@pytest.mark.asyncio
async def test_safe_reads_return_data_or_empty_mapping():
    manager = DescribeManager(
        object(),
        entities=_Entities(_AsyncResult({})),
        renderer=_Renderer(_AsyncResult([])),
        measurements=_Measurements(_AsyncResult({"bodies": {}})),
        featurescript=_FeatureScript(areas=_AsyncResult(_area_response())),
        partstudio=_PartStudio(_AsyncResult({})),
    )

    assert await manager._mass_props_safe("d", "w", "e") == {"bodies": {}}
    assert await manager._fetch_face_areas("d", "w", "e") == {
        "synthetic-plane": 0.0002,
        "synthetic-sliver": 0.00000001,
    }


def test_bbox_parser_ignores_bad_entries_and_requires_both_vectors():
    raw = _bbox_response()
    raw["result"]["value"].insert(0, "bad")
    raw["result"]["value"].insert(1, {"key": "bad", "value": {}})

    assert _parse_bbox_response(raw) == {
        "minCorner": {"x": 0.0, "y": 0.0, "z": 0.0},
        "maxCorner": {"x": 0.02, "y": 0.01, "z": 0.005},
    }
    assert _parse_bbox_response([]) is None
    assert _parse_bbox_response({"result": []}) is None
