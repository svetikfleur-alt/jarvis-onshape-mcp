import pytest

from onshape_mcp.api.geometry_diff import compute_diff


def _point(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": x, "y": y, "z": z}


def _plane(face_id: str, normal: dict[str, float]) -> dict[str, object]:
    return {
        "id": face_id,
        "surface": {
            "type": "plane",
            "origin": _point(0.001, 0.002, 0.003),
            "normal": normal,
        },
    }


def test_compute_diff_reports_geometry_bbox_and_aggregate_volume_changes() -> None:
    retained_face = _plane("retained", {"x": 1.0, "y": 0.0, "z": 0.0})
    retained_edge = {
        "id": "retained-edge",
        "curve": {"type": "line"},
        "geometry": {
            "startPoint": _point(0.0, 0.0, 0.0),
            "endPoint": _point(0.001, 0.0, 0.0),
        },
    }
    bodies_before = [
        {
            "faces": [retained_face, _plane("removed-x", {"x": -2.0, "y": 0.0, "z": 0.0})],
            "edges": [
                retained_edge,
                {
                    "id": "removed-edge",
                    "curve": {"type": "line"},
                    "geometry": {
                        "startPoint": _point(0.0, 0.0, 0.0),
                        "endPoint": _point(0.003, 0.004, 0.0),
                    },
                },
                {"id": "removed-unknown"},
            ],
            "vertices": [
                {"point": _point(-0.001, -0.002, -0.003)},
                {"point": _point(0.002, 0.004, 0.006)},
                {"point": None},
            ],
        }
    ]
    bodies_after = [
        {
            "faces": [
                retained_face,
                _plane("added-y", {"x": 0.0, "y": 3.0, "z": 0.0}),
                _plane("added-neg-y", {"x": 0.0, "y": -3.0, "z": 0.0}),
                _plane("added-z", {"x": 0.0, "y": 0.0, "z": 4.0}),
                _plane("added-neg-z", {"x": 0.0, "y": 0.0, "z": -4.0}),
                _plane("added-zero", {"x": 0.0, "y": 0.0, "z": 0.0}),
                _plane("added-diagonal", {"x": 1.0, "y": 1.0, "z": 0.0}),
                {
                    "id": "added-cylinder",
                    "surface": {
                        "type": "cylinder",
                        "origin": {"x": 0.005},
                        "radius": 0.0025,
                    },
                },
                {"id": "added-other"},
            ],
            "edges": [
                retained_edge,
                {
                    "id": "added-edge",
                    "curve": {"type": "circle"},
                    "geometry": {
                        "startPoint": _point(-0.002, -0.001, 0.0),
                        "endPoint": _point(0.004, 0.005, 0.006),
                    },
                },
                {
                    "id": "added-incomplete",
                    "curve": {"type": "other"},
                    "geometry": {"startPoint": _point(0.0, 0.0, 0.0)},
                },
            ],
        },
        {},
    ]

    result = compute_diff(
        bodies_before,
        bodies_after,
        mass_before={"bodies": {"-all-": {"volume": [0.000002]}}},
        mass_after={
            "bodies": {
                "body-a": {"volume": [0.000001]},
                "body-b": {"volume": [0.0000015]},
                "body-unknown": {"volume": []},
            }
        },
    )

    assert result["body_count_before"] == 1
    assert result["body_count_after"] == 2
    assert result["bbox_before_mm"] == {
        "x_min_mm": -1.0,
        "x_max_mm": 2.0,
        "y_min_mm": -2.0,
        "y_max_mm": 4.0,
        "z_min_mm": -3.0,
        "z_max_mm": 6.0,
    }
    assert result["bbox_after_mm"] == {
        "x_min_mm": -2.0,
        "x_max_mm": 4.0,
        "y_min_mm": -1.0,
        "y_max_mm": 5.0,
        "z_min_mm": 0.0,
        "z_max_mm": 6.0,
    }
    added_faces = {face["id"]: face for face in result["faces_added"]}
    assert added_faces["added-y"]["description"].startswith("plane / normal +Y")
    assert added_faces["added-neg-y"]["description"].startswith("plane / normal -Y")
    assert added_faces["added-z"]["description"].startswith("plane / normal +Z")
    assert added_faces["added-neg-z"]["description"].startswith("plane / normal -Z")
    assert "normal" not in added_faces["added-zero"]["description"]
    assert "normal" not in added_faces["added-diagonal"]["description"]
    assert added_faces["added-cylinder"] == {
        "id": "added-cylinder",
        "type": "CYLINDER",
        "description": "cylinder / origin (5.0,0.0,0.0) mm / radius 2.50 mm",
    }
    assert added_faces["added-other"]["type"] == "OTHER"
    assert result["faces_removed"][0]["description"].startswith("plane / normal -X")
    added_edges = {edge["id"]: edge for edge in result["edges_added"]}
    assert added_edges["added-edge"]["length_mm"] == pytest.approx(10.3923048454)
    assert "length_mm" not in added_edges["added-incomplete"]
    removed_edges = {edge["id"]: edge for edge in result["edges_removed"]}
    assert removed_edges["removed-edge"]["length_mm"] == 5.0
    assert "length_mm" not in removed_edges["removed-unknown"]
    assert result["volume_before_mm3"] == pytest.approx(2000.0)
    assert result["volume_after_mm3"] == pytest.approx(2500.0)
    assert result["volume_delta_mm3"] == pytest.approx(500.0)
    assert result["summary"] == "volume +500.0 mm³; faces +8/-1; edges +2/-2; bodies 1 → 2"


@pytest.mark.parametrize(
    "mass_properties",
    [None, {}, {"bodies": {}}, {"bodies": {"-all-": {"volume": []}}}],
)
def test_unavailable_mass_properties_do_not_create_numeric_volume(
    mass_properties: dict[str, object] | None,
) -> None:
    result = compute_diff([], [], mass_before=mass_properties)

    assert "volume_before_mm3" not in result


def test_negative_volume_delta_is_not_prefixed_with_plus() -> None:
    result = compute_diff(
        [],
        [],
        mass_before={"bodies": {"-all-": {"volume": [0.000002]}}},
        mass_after={"bodies": {"-all-": {"volume": [0.000001]}}},
    )

    assert result["summary"] == "volume -1000.0 mm³"


def test_translation_keeps_stable_topology_ids_out_of_added_removed_lists() -> None:
    before = [
        {
            "id": "BODY-A",
            "faces": [
                {
                    "id": "FACE-A",
                    "surface": {
                        "type": "plane",
                        "origin": _point(0.0, 0.0, 0.0),
                        "normal": _point(0.0, 0.0, 1.0),
                    },
                }
            ],
            "edges": [
                {
                    "id": "EDGE-A",
                    "curve": {"type": "line"},
                    "geometry": {
                        "startPoint": _point(0.0, 0.0, 0.0),
                        "endPoint": _point(0.01, 0.0, 0.0),
                    },
                }
            ],
            "vertices": [
                {"id": "VERTEX-A", "point": _point(0.0, 0.0, 0.0)},
                {"id": "VERTEX-B", "point": _point(0.01, 0.0, 0.0)},
            ],
        }
    ]
    after = [
        {
            "id": "BODY-A",
            "faces": [
                {
                    "id": "FACE-A",
                    "surface": {
                        "type": "plane",
                        "origin": _point(0.02, 0.0, 0.0),
                        "normal": _point(0.0, 0.0, 1.0),
                    },
                }
            ],
            "edges": [
                {
                    "id": "EDGE-A",
                    "curve": {"type": "line"},
                    "geometry": {
                        "startPoint": _point(0.02, 0.0, 0.0),
                        "endPoint": _point(0.03, 0.0, 0.0),
                    },
                }
            ],
            "vertices": [
                {"id": "VERTEX-A", "point": _point(0.02, 0.0, 0.0)},
                {"id": "VERTEX-B", "point": _point(0.03, 0.0, 0.0)},
            ],
        }
    ]

    result = compute_diff(before, after)

    assert result["faces_added"] == []
    assert result["faces_removed"] == []
    assert result["edges_added"] == []
    assert result["edges_removed"] == []
    assert result["bbox_before_mm"] != result["bbox_after_mm"]
    assert result["summary"] == "bounding box changed"
