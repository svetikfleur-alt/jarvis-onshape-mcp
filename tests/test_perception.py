"""Offline contracts for bounded engineering perception projections."""

from __future__ import annotations

import json

import pytest

from onshape_mcp.perception import inspect_feature_context, inspect_sketch_health


def _string(parameter_id: str, value: str) -> dict[str, object]:
    return {
        "btType": "BTMParameterString-149",
        "parameterId": parameter_id,
        "value": value,
    }


def _constraint(
    constraint_type: str,
    entity_id: str,
    *parameters: dict[str, object],
) -> dict[str, object]:
    return {
        "constraintType": constraint_type,
        "entityId": entity_id,
        "parameters": list(parameters),
    }


def _sketch_document(
    *,
    status: str = "OK",
    messages: list[dict[str, str]] | None = None,
) -> dict[str, object]:
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
                        "btType": "BTMSketchCurveSegment-155",
                        "entityId": "line-a",
                        "geometry": {"btType": "BTCurveGeometryLine-117"},
                    },
                    {
                        "btType": "BTMSketchCurve-4",
                        "entityId": "circle-a",
                        "geometry": {"btType": "BTCurveGeometryCircle-115"},
                    },
                    {"btType": "BTMSketchPoint-279", "entityId": "point-a"},
                ],
                "constraints": [
                    _constraint(
                        "DISTANCE",
                        "dimension-a",
                        _string("localFirst", "line-a"),
                        _string("externalSecond", "edge-safe"),
                    ),
                    _constraint(
                        "HORIZONTAL",
                        "horizontal-a",
                        _string("localFirst", "line-a"),
                    ),
                ],
            }
        ],
        "featureStates": {
            "sketch-safe": {
                "featureStatus": status,
                "messages": messages
                if messages is not None
                else [{"message": "provider detail intentionally not copied"}],
            }
        },
    }


def _feature(
    feature_id: str,
    feature_type: str,
    *,
    parameters: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "btType": "BTMFeature-134",
        "featureId": feature_id,
        "featureType": feature_type,
        "name": feature_id,
        "suppressed": False,
        "parameters": parameters or [],
    }


def _feature_reference(parameter_id: str, feature_id: str) -> dict[str, object]:
    return {
        "btType": "BTMParameterFeature-200",
        "parameterId": parameter_id,
        "value": feature_id,
    }


def test_sketch_health_returns_engineering_counts_without_solver_invention() -> None:
    result = inspect_sketch_health(_sketch_document(), sketch_feature_id="sketch-safe")

    assert result["contract"] == "jarvis.sketch_health.v1"
    assert result["sketch"] == {
        "feature_id": "sketch-safe",
        "name": "Profile",
        "status": "OK",
    }
    assert result["geometry"] == {
        "entity_count": 3,
        "counts_by_type": {"circle": 1, "line": 1, "point": 1},
    }
    assert result["constraints"]["constraint_count"] == 2
    assert result["constraints"]["dimensional_count"] == 1
    assert result["constraints"]["geometric_count"] == 1
    assert result["references"]["external_reference_count"] == 1
    assert result["references"]["evidence"]["rows"] == [
        {
            "constraint_id": "dimension-a",
            "constraint_type": "DISTANCE",
            "references": {"externalSecond": "edge-safe"},
        }
    ]
    assert result["constraint_health"] == {
        "state": "UNKNOWN",
        "remaining_dof": "unavailable",
        "source": "unavailable",
    }
    assert "FULLY_CONSTRAINED" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "expected_warnings"),
    [
        ("INFO", []),
        ("OK", []),
        ("WARNING", ["Sketch regeneration status is WARNING"]),
        ("ERROR", ["Sketch regeneration status is ERROR"]),
    ],
)
def test_sketch_health_classifies_regeneration_status(
    status: str,
    expected_warnings: list[str],
) -> None:
    result = inspect_sketch_health(
        _sketch_document(status=status, messages=[]),
        sketch_feature_id="sketch-safe",
    )

    assert result["warnings"] == expected_warnings


def test_sketch_health_surfaces_regeneration_error_without_copying_raw_messages() -> None:
    result = inspect_sketch_health(
        _sketch_document(status="ERROR"), sketch_feature_id="sketch-safe"
    )

    assert result["sketch"]["status"] == "ERROR"
    assert result["warnings"] == [
        "Sketch regeneration status is ERROR",
        "Authoritative feature state contains 1 regeneration message(s)",
    ]
    assert "provider detail intentionally not copied" not in json.dumps(result)


def test_large_sketch_response_contains_only_bounded_reference_evidence() -> None:
    document = _sketch_document()
    sketch = document["features"][0]
    sketch["entities"] = [
        {"btType": "BTMSketchPoint-279", "entityId": f"point-{index}"}
        for index in range(500)
    ]
    sketch["constraints"] = [
        _constraint(
            "COINCIDENT",
            f"constraint-{index}",
            _string("externalFirst", f"external-{index}"),
        )
        for index in range(500)
    ]

    result = inspect_sketch_health(document, sketch_feature_id="sketch-safe")

    assert result["geometry"]["entity_count"] == 500
    assert result["constraints"]["constraint_count"] == 500
    assert result["references"]["external_reference_count"] == 500
    evidence = result["references"]["evidence"]
    assert evidence["count"] == 500
    assert evidence["returned_count"] == 12
    assert len(evidence["rows"]) == 12
    assert evidence["truncated"] is True
    assert "entities" not in result
    assert "constraint-499" not in json.dumps(result)


def test_feature_context_preserves_explicit_upstream_and_immediate_downstream() -> None:
    document = {
        "sourceMicroversion": "microversion-feature-safe",
        "features": [
            _feature("sketch-safe", "newSketch"),
            _feature(
                "extrude-safe",
                "extrude",
                parameters=[
                    _feature_reference("profile", "sketch-safe"),
                    {
                        "btType": "BTMParameterQuantity-147",
                        "parameterId": "depth",
                        "expression": "12 mm",
                        "value": 0.012,
                        "units": "m",
                    },
                ],
            ),
            _feature(
                "pattern-safe",
                "linearPattern",
                parameters=[_feature_reference("seed", "extrude-safe")],
            ),
        ],
        "featureStates": {
            "sketch-safe": {"featureStatus": "OK"},
            "extrude-safe": {"featureStatus": "OK"},
            "pattern-safe": {"featureStatus": "OK"},
        },
    }

    result = inspect_feature_context(document, "extrude-safe")

    assert result["contract"] == "jarvis.feature_context.v1"
    assert result["feature"] == {
        "id": "extrude-safe",
        "name": "extrude-safe",
        "type": "extrude",
        "status": "OK",
        "suppressed": False,
    }
    assert result["canonical_parameters"]["items"]["depth"] == {
        "expression": "12 mm",
        "value": 0.012,
        "units": "m",
    }
    assert result["upstream"]["feature_ids"] == ["sketch-safe"]
    assert result["upstream"]["count"] == 1
    assert result["downstream"]["immediate_feature_ids"] == ["pattern-safe"]
    assert result["downstream"]["count"] == 1
    assert result["reference_health"]["confirmed"]["count"] == 1
    assert result["source_revision"] == "microversion-feature-safe"


def test_feature_context_keeps_unknown_geometry_reference_unresolved() -> None:
    document = {
        "features": [
            _feature(
                "fillet-safe",
                "fillet",
                parameters=[
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
            )
        ]
    }

    result = inspect_feature_context(document, "fillet-safe")

    assert result["upstream"]["feature_ids"] == []
    assert result["reference_health"]["confirmed"]["count"] == 0
    assert result["reference_health"]["unresolved"]["count"] == 1
    assert result["reference_health"]["warnings"] == [
        "1 reference(s) remain unresolved; no dependency edges were invented"
    ]


def test_large_feature_tree_reports_total_counts_but_bounds_rows_and_parameters() -> None:
    features = [_feature("root-safe", "newSketch")]
    features.extend(
        _feature(
            f"child-{index:03d}",
            "extrude",
            parameters=[
                _feature_reference("source", "root-safe"),
                {
                    "btType": "BTMParameterString-149",
                    "parameterId": f"label-{index}",
                    "value": "x" * 400,
                },
            ],
        )
        for index in range(75)
    )
    document = {"features": features}

    root = inspect_feature_context(document, "root-safe", dependency_limit=5)
    child = inspect_feature_context(document, "child-000", parameter_limit=1)

    assert root["downstream"]["count"] == 75
    assert root["downstream"]["returned_count"] == 5
    assert len(root["downstream"]["immediate_feature_ids"]) == 5
    assert root["downstream"]["truncated"] is True
    assert child["canonical_parameters"]["count"] == 2
    assert child["canonical_parameters"]["returned_count"] == 1
    assert child["canonical_parameters"]["truncated"] is True
    assert len(json.dumps(root).encode("utf-8")) < 8_000
