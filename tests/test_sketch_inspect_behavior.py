import math

import pytest

from onshape_mcp.api.sketch_inspect import find_sketch, inspect_sketch, list_sketches


def _string(parameter_id: str, value: str) -> dict[str, object]:
    return {
        "btType": "BTMParameterString-149",
        "parameterId": parameter_id,
        "value": value,
    }


def _quantity(
    parameter_id: str,
    *,
    expression: str = "",
    value: float = 0.0,
) -> dict[str, object]:
    return {
        "btType": "BTMParameterQuantity-147",
        "parameterId": parameter_id,
        "expression": expression,
        "value": value,
    }


def _constraint(
    constraint_type: str,
    entity_id: str,
    *parameters: object,
) -> dict[str, object]:
    return {
        "constraintType": constraint_type,
        "entityId": entity_id,
        "parameters": list(parameters),
    }


def _complete_sketch() -> dict[str, object]:
    return {
        "btType": "BTMSketch-151",
        "featureId": "sketch-1",
        "name": "Profile",
        "parameters": [
            None,
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "sketchPlane",
                "queries": [{"deterministicIds": ["FACE-A"]}, {}],
            },
        ],
        "entities": [
            {
                "btType": "BTMSketchCurveSegment-155",
                "entityId": "line",
                "isConstruction": True,
                "startPointId": "line.start",
                "endPointId": "line.end",
                "startParam": 0.0,
                "endParam": 0.003,
                "geometry": {
                    "btType": "BTCurveGeometryLine-117",
                    "pntX": 0.001,
                    "pntY": 0.002,
                    "dirX": 0.0,
                    "dirY": 1.0,
                },
            },
            {
                "btType": "BTMSketchCurveSegment-155",
                "entityId": "arc",
                "startParam": 0.0,
                "endParam": math.pi / 2,
                "geometry": {
                    "btType": "BTCurveGeometryCircle-115",
                    "xCenter": 0.01,
                    "yCenter": 0.02,
                    "radius": 0.005,
                    "xDir": 0.0,
                    "yDir": 1.0,
                    "clockwise": True,
                },
            },
            {
                "btType": "BTMSketchCurve-4",
                "entityId": "circle",
                "geometry": {
                    "btType": "BTCurveGeometryCircle-115",
                    "xCenter": -0.01,
                    "yCenter": -0.02,
                    "radius": 0.004,
                },
            },
            {
                "btType": "BTMSketchCurveSegment-155",
                "entityId": "full-sweep",
                "startParam": 0.0,
                "endParam": 2 * math.pi,
                "geometry": {
                    "btType": "BTCurveGeometryCircle-115",
                    "radius": 0.002,
                },
            },
            {
                "btType": "BTMSketchPoint-279",
                "entityId": "point",
                "x": 0.003,
                "y": -0.004,
            },
            {"btType": "BTMUnknown-1", "entityId": "other"},
        ],
        "constraints": [
            _constraint(
                "DISTANCE",
                "c-distance",
                None,
                {},
                _string("localFirst", "line.start"),
                _string("localSecond", "line.end"),
                _quantity("length", expression="3 mm"),
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "direction",
                    "value": "HORIZONTAL",
                },
            ),
            _constraint(
                "LENGTH",
                "c-length",
                _string("localFirst", "line"),
                _quantity("length", value=0.02),
            ),
            _constraint(
                "DIAMETER",
                "c-diameter",
                _string("localFirst", "circle"),
                _quantity("length", expression="8 mm"),
            ),
            _constraint(
                "RADIUS",
                "c-radius",
                _string("localFirst", "arc"),
                _quantity("length", expression="5 mm"),
            ),
            _constraint(
                "ANGLE",
                "c-angle",
                _string("localFirst", "line"),
                _string("localSecond", "arc"),
                _quantity("angle", expression="45 deg"),
            ),
            _constraint(
                "CONCENTRIC",
                "c-concentric",
                _string("externalFirst", "FACE-X"),
                _string("localSecond", "circle"),
            ),
            _constraint(
                "HORIZONTAL",
                "c-horizontal",
                _string("localFirst", "line"),
                _string("externalSecond", "EDGE-X"),
            ),
            _constraint("VERTICAL", "c-vertical", _string("localFirst", "line")),
            _constraint("FIX", "c-fix", _string("localFirst", "point")),
            _constraint(
                "CUSTOM",
                "c-custom",
                {
                    "btType": "BTMParameterUnknown-999",
                    "parameterId": "localFirst",
                    "value": "other",
                },
                _string("localSecond", "point"),
            ),
            _constraint(
                "DISTANCE",
                "c-boolean-direction",
                _string("localFirst", "point"),
                _quantity("length", expression="1 mm"),
                {
                    "btType": "BTMParameterBoolean-144",
                    "parameterId": "direction",
                    "value": 1,
                },
            ),
        ],
    }


def test_inspect_sketch_summarizes_supported_geometry_and_constraints() -> None:
    sketch = _complete_sketch()
    result = inspect_sketch(
        {
            "features": [sketch],
            "featureStates": {"sketch-1": {"featureStatus": "OK"}},
        }
    )

    assert result["name"] == "Profile"
    assert result["feature_id"] == "sketch-1"
    assert result["status"] == "OK"
    assert result["plane_query"] == ["FACE-A"]
    entities = {entity["id"]: entity for entity in result["entities"]}
    assert entities["line"]["kind"] == "line"
    assert entities["line"]["start_mm"] == (1.0, 2.0)
    assert entities["line"]["end_mm"] == (1.0, 5.0)
    assert entities["line"]["length_mm"] == 3.0
    assert entities["line"]["summary"].endswith("construction")
    assert entities["arc"]["kind"] == "arc"
    assert entities["arc"]["start_mm"] == pytest.approx((10.0, 25.0))
    assert entities["arc"]["end_mm"] == pytest.approx((15.0, 20.0))
    assert entities["arc"]["sweep_deg"] == 90.0
    assert entities["circle"]["kind"] == "circle"
    assert entities["circle"]["center_mm"] == (-10.0, -20.0)
    assert entities["full-sweep"]["kind"] == "circle"
    assert entities["point"]["point_mm"] == (3.0, -4.0)
    assert entities["other"]["kind"] == "other"

    constraints = {constraint["id"]: constraint for constraint in result["constraints"]}
    assert constraints["c-distance"]["summary"] == (
        "DISTANCE      line.start <-> line.end  (HORIZONTAL)  = 3 mm"
    )
    assert constraints["c-length"]["length"] == 0.02
    assert constraints["c-diameter"]["summary"].endswith("circle  = 8 mm")
    assert constraints["c-radius"]["summary"].endswith("arc  = 5 mm")
    assert constraints["c-angle"]["summary"].endswith("line <-> arc  = 45 deg")
    assert "external=FACE-X" in constraints["c-concentric"]["summary"]
    assert "(external=EDGE-X)" in constraints["c-horizontal"]["summary"]
    assert constraints["c-vertical"]["summary"].endswith("line")
    assert constraints["c-fix"]["summary"].endswith("point")
    assert constraints["c-custom"]["summary"].endswith("other <-> point")
    assert "(True)" in constraints["c-boolean-direction"]["summary"]
    assert "ENTITIES (6):" in result["text"]
    assert "CONSTRAINTS (11):" in result["text"]
    assert result["constraint_quality"] == {
        "authoritative_status_available": False,
        "fully_constrained": None,
        "degrees_of_freedom": None,
        "entity_count": 6,
        "constraint_count": 11,
        "production_complete": None,
        "quality_state": "UNVERIFIED",
        "reason": (
            "The authoritative feature response does not expose sketch degrees "
            "of freedom or fully-constrained status."
        ),
    }
    assert "CONSTRAINT QUALITY: UNVERIFIED" in result["text"]


def test_inspect_empty_sketch_reports_unknown_state_and_empty_sections() -> None:
    result = inspect_sketch(
        {
            "features": [
                {
                    "btType": "BTMSketch-151",
                    "featureId": "empty",
                    "name": "",
                    "parameters": [
                        {
                            "btType": "BTMParameterString-149",
                            "parameterId": "sketchPlane",
                            "value": "not-a-query",
                        }
                    ],
                }
            ]
        }
    )

    assert result["status"] == "?"
    assert result["plane_query"] == []
    assert "ENTITIES: none" in result["text"]
    assert "CONSTRAINTS: none" in result["text"]
    assert result["constraint_quality"]["quality_state"] == "UNVERIFIED"


def test_find_sketch_resolves_by_id_or_exact_name() -> None:
    first = {"btType": "BTMSketch-151", "featureId": "one", "name": "First"}
    second = {"btType": "BTMSketch-151", "featureId": "two", "name": "Second"}
    doc = {"features": [first, second]}

    assert find_sketch(doc, sketch_feature_id="two", sketch_name="First") is second
    assert find_sketch(doc, sketch_name="First") is first


@pytest.mark.parametrize(
    ("features", "kwargs", "message"),
    [
        (
            [{"btType": "BTMPart-1", "featureId": "solid", "name": "Solid"}],
            {"sketch_feature_id": "solid"},
            "is not a sketch",
        ),
        (
            [{"btType": "BTMSketch-151", "featureId": "one", "name": "First"}],
            {"sketch_feature_id": "missing"},
            "not found",
        ),
        (
            [
                {"btType": "BTMSketch-151", "featureId": "one", "name": "Same"},
                {"btType": "BTMSketch-151", "featureId": "two", "name": "Same"},
            ],
            {"sketch_name": "Same"},
            "multiple sketches",
        ),
        (
            [{"btType": "BTMSketch-151", "featureId": "one", "name": "First"}],
            {"sketch_name": "Missing"},
            "no sketch named",
        ),
        (
            [
                {"btType": "BTMSketch-151", "featureId": "one", "name": "First"},
                {"btType": "BTMSketch-151", "featureId": "two", "name": "Second"},
            ],
            {},
            "pass sketchFeatureId or sketchName",
        ),
    ],
)
def test_find_sketch_rejects_ambiguous_or_invalid_selections(
    features: list[dict[str, str]],
    kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        find_sketch({"features": features}, **kwargs)


def test_list_sketches_skips_other_features_and_counts_optional_lists() -> None:
    result = list_sketches(
        {
            "features": [
                {"btType": "BTMPart-1", "featureId": "solid"},
                {
                    "btType": "BTMSketch-151",
                    "featureId": "one",
                    "name": "Profile",
                    "entities": [{}, {}],
                    "constraints": [{}],
                },
                {"btType": "BTMSketch-151"},
            ],
            "featureStates": {"one": {"featureStatus": "OK"}},
        }
    )

    assert result == [
        {
            "feature_id": "one",
            "name": "Profile",
            "status": "OK",
            "entity_count": 2,
            "constraint_count": 1,
        },
        {
            "feature_id": "",
            "name": "",
            "status": "?",
            "entity_count": 0,
            "constraint_count": 0,
        },
    ]
