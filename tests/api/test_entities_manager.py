"""Offline contract tests for entity enumeration and filtering."""

import pytest

from onshape_mcp.api.entities import EntityManager


def _xyz(x, y, z):
    return {"x": x, "y": y, "z": z}


def _bodydetails():
    return {
        "bodies": [
            {
                "id": "BODY-A",
                "type": "solid",
                "faces": [
                    {
                        "id": "FACE-TOP",
                        "surface": {
                            "type": "plane",
                            "origin": _xyz(0, 0, 0.01),
                            "normal": _xyz(0, 0, 1),
                        },
                    },
                    {
                        "id": "FACE-HOLE",
                        "surface": {
                            "type": "cylinder",
                            "origin": _xyz(0.005, 0, 0),
                            "axis": _xyz(0, 0, 1),
                            "radius": 0.002,
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "EDGE-X",
                        "curve": {"type": "line"},
                        "geometry": {
                            "startPoint": _xyz(0, 0, 0.01),
                            "endPoint": _xyz(0.01, 0, 0.01),
                            "midPoint": _xyz(0.005, 0, 0.01),
                        },
                        "vertices": ["VERTEX-A", "VERTEX-B"],
                    },
                    {"id": "EDGE-UNKNOWN", "curve": {}, "geometry": {}},
                ],
                "vertices": [
                    {"id": "VERTEX-A", "point": _xyz(0, 0, 0.01)},
                    {"id": "VERTEX-MISSING"},
                ],
            },
            {
                "id": "BODY-B",
                "type": "solid",
                "faces": [],
                "edges": [
                    {
                        "id": "EDGE-Y",
                        "curve": {"type": "line"},
                        "geometry": {
                            "startPoint": _xyz(0, 0, 0.02),
                            "endPoint": _xyz(0, 0.02, 0.02),
                            "midPoint": _xyz(0, 0.01, 0.02),
                        },
                    }
                ],
                "vertices": [],
            },
        ]
    }


def _frame_response():
    components = [0, 0, -1, 1, 0, 0, 0, -1, 0]
    return {
        "result": {
            "value": [
                {
                    "key": {"value": "FACE-TOP"},
                    "value": {"value": [{"value": item} for item in components]},
                }
            ]
        }
    }


class _Client:
    def __init__(self, bodydetails=None, frames=None, *, fail_frames=False):
        self.bodydetails = bodydetails if bodydetails is not None else _bodydetails()
        self.frames = frames if frames is not None else _frame_response()
        self.fail_frames = fail_frames
        self.calls = []

    async def get(self, path):
        self.calls.append(("GET", path, None))
        return self.bodydetails

    async def post(self, path, data):
        self.calls.append(("POST", path, data))
        if self.fail_frames:
            raise RuntimeError("frame probe unavailable")
        return self.frames


@pytest.mark.asyncio
async def test_list_entities_enriches_all_kinds_and_reports_counts():
    client = _Client()

    result = await EntityManager(client).list_entities("DOC", "WORK", "ELEMENT")

    first = result["bodies"][0]
    assert first["faces"][0]["outward_axis"] == "-Z"
    assert first["faces"][0]["sketch_x_axis"] == "+X"
    assert first["faces"][1]["type"] == "CYLINDER"
    assert first["faces"][1]["radius"] == pytest.approx(0.002)
    assert first["edges"][0]["direction_axis"] == "+X"
    assert first["edges"][0]["length"] == pytest.approx(0.01)
    assert first["edges"][0]["vertex_ids"] == ["VERTEX-A", "VERTEX-B"]
    assert first["edges"][1]["type"] == "OTHER"
    assert first["edges"][1]["direction"] is None
    assert first["vertices"][0]["description"].endswith("(0.0,0.0,10.0) mm")
    assert first["vertices"][1]["description"] == "vertex"
    assert result["original_counts"]["BODY-A"] == {
        "faces": 2,
        "edges": 2,
        "vertices": 2,
    }
    assert result["filtered_counts"]["BODY-A"] == result["original_counts"][
        "BODY-A"
    ]
    assert "body[1] id=BODY-B type=solid faces=0 edges=1 vertices=0" in result[
        "summary"
    ]
    assert [call[0] for call in client.calls] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_edge_only_filters_skip_frame_probe_and_limit_body_before_serializing():
    client = _Client()

    result = await EntityManager(client).list_entities(
        "D",
        "W",
        "E",
        kinds=["edges"],
        body_index=1,
        geometry_type=" line ",
        at_z_mm=20,
        at_z_tol_mm=0.1,
        length_range_mm=[19, 21],
    )

    assert len(result["bodies"]) == 1
    assert result["bodies"][0]["body_id"] == "BODY-B"
    assert [edge["id"] for edge in result["bodies"][0]["edges"]] == ["EDGE-Y"]
    assert "faces" not in result["bodies"][0]
    assert result["filters"] == {
        "kinds": ["edges"],
        "body_index": 1,
        "geometry_type": "LINE",
        "outward_axis": None,
        "at_z_mm": 20,
        "at_z_tol_mm": 0.1,
        "radius_range_mm": None,
        "length_range_mm": [19, 21],
    }
    assert [call[0] for call in client.calls] == ["GET"]


@pytest.mark.asyncio
async def test_face_probe_failure_falls_back_to_defining_normal():
    client = _Client(fail_frames=True)

    result = await EntityManager(client).list_entities(
        "D", "W", "E", kinds=["faces"], outward_axis="+Z"
    )

    assert [face["id"] for face in result["bodies"][0]["faces"]] == ["FACE-TOP"]
    assert result["bodies"][0]["faces"][0]["outward_axis"] is None


@pytest.mark.asyncio
async def test_empty_bodydetails_returns_explicit_empty_summary():
    result = await EntityManager(_Client(bodydetails={})).list_entities("D", "W", "E")

    assert result["bodies"] == []
    assert result["summary"] == "no bodies"
    assert result["original_counts"] == {}
    assert result["filtered_counts"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"outward_axis": "north"}, "outward_axis must be one of"),
        ({"radius_range_mm": [1]}, "radius_range_mm must be"),
        ({"length_range_mm": [1, 2, 3]}, "length_range_mm must be"),
    ],
)
async def test_invalid_filters_fail_before_any_client_io(kwargs, message):
    client = _Client()

    with pytest.raises(ValueError, match=message):
        await EntityManager(client).list_entities("D", "W", "E", **kwargs)

    assert client.calls == []
