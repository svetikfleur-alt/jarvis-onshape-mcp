"""Offline behavior tests for numeric entity measurements."""

import math

import pytest

from onshape_mcp.api.measurements import (
    MeasurementManager,
    _angle_between_unit_vectors,
    _cross,
    _direction_of_edge,
    _point_and_dir,
    _signed_distance_along,
)


def _xyz(x, y, z):
    return {"x": x, "y": y, "z": z}


def _face(entity_id, origin, normal=None, *, kind="plane", axis=None):
    surface = {"type": kind, "origin": _xyz(*origin)}
    if normal is not None:
        surface["normal"] = _xyz(*normal)
    if axis is not None:
        surface["axis"] = _xyz(*axis)
    return {"id": entity_id, "surface": surface}


def _edge(entity_id, start, end, midpoint, *, kind="line"):
    return {
        "id": entity_id,
        "curve": {"type": kind},
        "geometry": {
            "startPoint": _xyz(*start),
            "endPoint": _xyz(*end),
            "midPoint": _xyz(*midpoint),
        },
    }


def _vertex(entity_id, point):
    return {"id": entity_id, "point": _xyz(*point)}


class _Client:
    def __init__(self, bodydetails):
        self.bodydetails = bodydetails
        self.paths = []

    async def get(self, path):
        self.paths.append(path)
        if path.endswith("/bodydetails"):
            return self.bodydetails
        return {"requested": path}


@pytest.mark.asyncio
async def test_parallel_planes_report_acute_angle_and_perpendicular_separation():
    client = _Client(
        {
            "bodies": [
                {"faces": [_face("BOTTOM", (0, 0, 0), (0, 0, 1))]},
                {"faces": [_face("TOP", (0, 0, 0.02), (0, 0, -1))]},
            ]
        }
    )

    result = await MeasurementManager(client).measure(
        "doc", "workspace", "element", entity_a_id="BOTTOM", entity_b_id="TOP"
    )

    assert result["ok"] is True
    assert result["body_indices"] == [0, 1]
    assert result["parallel"] is True
    assert result["perpendicular"] is False
    assert result["angle_deg"] == pytest.approx(0.0)
    assert result["point_distance_mm"] == pytest.approx(20.0)
    assert result["projected_distance_mm"] == pytest.approx(20.0)
    assert result["notes"] == [
        "plane-to-plane perpendicular distance (faces parallel)"
    ]


@pytest.mark.asyncio
async def test_perpendicular_planes_do_not_claim_projected_plane_separation():
    client = _Client(
        {
            "bodies": [
                {
                    "faces": [
                        _face("XY", (0, 0, 0), (0, 0, 1)),
                        _face("YZ", (1, 0, 0), (1, 0, 0)),
                    ]
                }
            ]
        }
    )

    result = await MeasurementManager(client).measure(
        "d", "w", "e", entity_a_id="XY", entity_b_id="YZ"
    )

    assert result["perpendicular"] is True
    assert result["parallel"] is False
    assert result["angle_deg"] == pytest.approx(90.0)
    assert "projected_distance_m" not in result
    assert "notes" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("a_id", "b_id", "expected_note"),
    [
        ("PLANE", "EDGE", "point-to-plane distance (b is a edge)"),
        ("EDGE", "PLANE", "point-to-plane distance (a is a edge)"),
    ],
)
async def test_plane_edge_projection_works_in_either_argument_order(
    a_id, b_id, expected_note
):
    client = _Client(
        {
            "bodies": [
                {
                    "faces": [_face("PLANE", (0, 0, 0), (0, 0, 1))],
                    "edges": [_edge("EDGE", (0, 0, 0.01), (0, 0, 0.03), (0, 0, 0.02))],
                }
            ]
        }
    )

    result = await MeasurementManager(client).measure(
        "d", "w", "e", entity_a_id=a_id, entity_b_id=b_id
    )

    assert result["projected_distance_mm"] == pytest.approx(20.0)
    assert expected_note in result["notes"]


@pytest.mark.asyncio
async def test_curved_faces_disclose_axis_origin_approximation_for_both_entities():
    client = _Client(
        {
            "bodies": [
                {
                    "faces": [
                        _face("CYL", (0, 0, 0), kind="cylinder", axis=(0, 0, 1)),
                        _face("CONE", (1, 0, 0), kind="cone", axis=(1, 0, 0)),
                    ]
                }
            ]
        }
    )

    result = await MeasurementManager(client).measure(
        "d", "w", "e", entity_a_id="CYL", entity_b_id="CONE"
    )

    assert result["angle_deg"] == pytest.approx(90.0)
    assert result["notes"] == [
        "entity_a is a cylinder face; representative point is its axis origin",
        "entity_b is a cone face; representative point is its axis origin",
    ]


@pytest.mark.asyncio
async def test_missing_entities_return_structured_failures_without_guessing():
    client = _Client({"bodies": [{"vertices": [_vertex("ONLY", (0, 0, 0))]}]})
    manager = MeasurementManager(client)

    missing_a = await manager.measure(
        "d", "w", "e", entity_a_id="ABSENT", entity_b_id="ONLY"
    )
    missing_b = await manager.measure(
        "d", "w", "e", entity_a_id="ONLY", entity_b_id="ABSENT"
    )

    assert missing_a == {"ok": False, "error": "entity ABSENT not found"}
    assert missing_b == {"ok": False, "error": "entity ABSENT not found"}


@pytest.mark.asyncio
async def test_degenerate_edge_and_vertex_still_report_representative_distance():
    client = _Client(
        {
            "bodies": [
                {
                    "edges": [_edge("POINT_EDGE", (0, 0, 0), (0, 0, 0), (0, 0, 0))],
                    "vertices": [_vertex("VERTEX", (0.003, 0.004, 0))],
                }
            ]
        }
    )

    result = await MeasurementManager(client).measure(
        "d", "w", "e", entity_a_id="POINT_EDGE", entity_b_id="VERTEX"
    )

    assert result["point_distance_mm"] == pytest.approx(5.0)
    assert "angle_rad" not in result


@pytest.mark.asyncio
async def test_mass_property_methods_use_the_correct_resource_shape():
    client = _Client({})
    manager = MeasurementManager(client)

    studio = await manager.mass_properties_part_studio("D", "W", "E")
    part = await manager.mass_properties_part("D", "W", "E", "P")

    assert studio["requested"] == "/api/v9/partstudios/d/D/w/W/e/E/massproperties"
    assert part["requested"] == "/api/v9/parts/d/D/w/W/e/E/partid/P/massproperties"


def test_numeric_helpers_handle_clamping_missing_geometry_and_unknown_kinds():
    assert _cross([1, 0, 0], [0, 1, 0]) == [0, 0, 1]
    assert _signed_distance_along([1, 1, 1], [4, 1, 1], [1, 0, 0]) == 3
    assert _angle_between_unit_vectors([2, 0, 0], [2, 0, 0]) == 0.0
    assert _direction_of_edge({"geometry": {"startPoint": _xyz(0, 0, 0)}}) is None
    assert _point_and_dir({}, "unsupported") == (None, None, None)
    assert math.isfinite(_angle_between_unit_vectors([1, 0, 0], [0, 1, 0]))
