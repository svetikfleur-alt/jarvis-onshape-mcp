"""Unit contracts for cached existing-model feature intelligence."""

from __future__ import annotations

import json

import pytest

from onshape_mcp.governance.features import FeatureIndex, FeatureNotFoundError


def _parameter(parameter_id: str, bt_type: str, **values: object) -> dict[str, object]:
    return {"parameterId": parameter_id, "btType": bt_type, **values}


def _feature(
    feature_id: str,
    name: str,
    feature_type: str,
    *,
    parameters: list[dict[str, object]] | None = None,
    suppressed: bool = False,
) -> dict[str, object]:
    return {
        "btType": "BTMFeature-134",
        "featureId": feature_id,
        "featureType": feature_type,
        "name": name,
        "suppressed": suppressed,
        "parameters": parameters or [],
    }


@pytest.fixture
def realistic_payload() -> dict[str, object]:
    features = [
        _feature(
            "sketch-1",
            "Base Profile",
            "newSketch",
            parameters=[
                _parameter(
                    "width",
                    "BTMParameterQuantity-147",
                    expression="25 mm",
                    value=0.025,
                    units="m",
                ),
                _parameter("mode", "BTMParameterEnum-145", value="ADD"),
                _parameter("label", "BTMParameterString-149", value="Primary width driver"),
                _parameter("enabled", "BTMParameterBoolean-144", value=True),
                _parameter(
                    "plane",
                    "BTMParameterQueryList-148",
                    queries=[
                        {
                            "btType": "BTMIndividualQuery-138",
                            "deterministicIds": ["JDC"],
                            "queryString": "query=qCompressed(...);",
                        }
                    ],
                ),
                _parameter(
                    "mystery",
                    "BTMParameterUnknown-999",
                    payload={"raw-payload-marker": "must-not-leak", "nested": [1, 2, 3]},
                ),
            ],
        ),
        _feature(
            "extrude-1",
            "Main Extrude",
            "extrude",
            parameters=[
                _parameter(
                    "entities",
                    "BTMParameterQueryList-148",
                    queries=[
                        {
                            "btType": "BTMIndividualSketchRegionQuery-140",
                            "featureId": "sketch-1",
                            "deterministicIds": ["JGC"],
                            "queryString": 'query=qSketchRegion(id + "sketch-1", true);',
                        }
                    ],
                )
            ],
        ),
        _feature(
            "fillet-1",
            "Edge Finish",
            "fillet",
            parameters=[
                _parameter(
                    "entities",
                    "BTMParameterQueryList-148",
                    queries=[
                        {
                            "btType": "BTMIndividualQuery-138",
                            "deterministicIds": ["edge-JHD"],
                        }
                    ],
                ),
                _parameter("note", "BTMParameterString-149", value="sketch-1"),
            ],
        ),
        _feature(
            "pattern-1",
            "Pattern",
            "linearPattern",
            parameters=[
                _parameter(
                    "seedFeature",
                    "BTMParameterFeature-200",
                    value="extrude-1",
                )
            ],
        ),
        _feature(
            "cycle-a",
            "Cycle A",
            "custom",
            parameters=[
                _parameter(
                    "sourceFeature",
                    "BTMParameterFeature-200",
                    featureId="cycle-b",
                )
            ],
        ),
        _feature(
            "cycle-b",
            "Cycle B",
            "custom",
            parameters=[
                _parameter(
                    "sourceFeature",
                    "BTMParameterFeature-200",
                    featureId="cycle-a",
                )
            ],
            suppressed=True,
        ),
    ]
    states = {
        "sketch-1": {"featureStatus": "OK"},
        "extrude-1": {"featureStatus": "OK"},
        "fillet-1": {"featureStatus": "WARNING"},
        "pattern-1": {"featureStatus": "OK"},
        "cycle-a": {"featureStatus": "OK"},
        "cycle-b": {"featureStatus": "ERROR"},
    }
    return {"features": features, "featureStates": states}


def test_index_search_is_stable_filterable_and_does_not_flatten_raw_payload(
    realistic_payload: dict[str, object],
) -> None:
    index = FeatureIndex.build(realistic_payload)

    assert [record.ordinal for record in index.records] == [1, 2, 3, 4, 5, 6]
    assert index.records[0].raw_index == 0
    assert index.records[0].parameter_ids == (
        "width",
        "mode",
        "label",
        "enabled",
        "plane",
        "mystery",
    )

    text_match = index.search(query="primary WIDTH", limit=20)
    filtered = index.search(
        feature_type="custom",
        status="error",
        suppressed=True,
        from_ordinal=5,
        to_ordinal=6,
        limit=20,
    )

    assert [row["featureId"] for row in text_match["rows"]] == ["sketch-1"]
    assert [row["featureId"] for row in filtered["rows"]] == ["cycle-b"]
    assert "raw-payload-marker" not in json.dumps(text_match)


def test_search_enforces_hard_row_cap() -> None:
    payload = {
        "features": [_feature(f"feature-{index}", f"Feature {index}", "custom") for index in range(70)]
    }

    result = FeatureIndex.build(payload).search(limit=500)

    assert result["total"] == 70
    assert result["returned"] == 50
    assert result["has_more"] is True
    assert len(result["rows"]) == 50


def test_inspection_normalizes_common_parameters_and_keeps_unknown_compact(
    realistic_payload: dict[str, object],
) -> None:
    detail = FeatureIndex.build(realistic_payload).inspect("sketch-1", max_parameters=80)
    parameters = {row["parameterId"]: row for row in detail["parameters"]}

    assert detail["ordinal"] == 1
    assert detail["parameter_count"] == 6
    assert detail["returned_parameters"] == 6
    assert detail["truncated"] is False
    assert parameters["width"]["value_summary"] == {
        "expression": "25 mm",
        "value": 0.025,
        "units": "m",
    }
    assert parameters["mode"]["value_summary"] == "ADD"
    assert parameters["label"]["value_summary"] == "Primary width driver"
    assert parameters["enabled"]["value_summary"] is True
    assert parameters["plane"]["value_summary"] == {"query_count": 1}
    assert parameters["plane"]["reference_summary"]["unresolved_count"] == 1
    assert parameters["mystery"] == {
        "parameterId": "mystery",
        "parameterType": "BTMParameterUnknown-999",
        "value_summary": "unsupported parameter type",
        "raw_available": True,
    }
    assert "raw-payload-marker" not in json.dumps(detail)


def test_inspection_parameter_cap_and_missing_feature_are_explicit(
    realistic_payload: dict[str, object],
) -> None:
    index = FeatureIndex.build(realistic_payload)

    detail = index.inspect("sketch-1", max_parameters=3)

    assert detail["returned_parameters"] == 3
    assert detail["truncated"] is True
    assert len(detail["parameters"]) == 3
    with pytest.raises(FeatureNotFoundError, match="missing"):
        index.inspect("missing")


def test_dependency_index_uses_only_explicit_evidence_and_builds_reverse_edges(
    realistic_payload: dict[str, object],
) -> None:
    index = FeatureIndex.build(realistic_payload)

    extrude = index.dependencies("extrude-1")
    sketch = index.dependencies("sketch-1")
    pattern = index.dependencies("pattern-1")
    fillet = index.dependencies("fillet-1")

    assert [(row["featureId"], row["evidenceType"]) for row in extrude["upstream"]] == [
        ("sketch-1", "EXPLICIT_QUERY_REFERENCE")
    ]
    assert [row["featureId"] for row in sketch["downstream"]] == ["extrude-1"]
    assert [(row["featureId"], row["evidenceType"]) for row in pattern["upstream"]] == [
        ("extrude-1", "EXACT_FEATURE_ID")
    ]
    assert fillet["upstream"] == []
    assert fillet["unresolved_references"] == [
        {
            "parameterId": "entities",
            "evidenceType": "UNRESOLVED_GEOMETRY_REFERENCE",
            "referenceType": "BTMIndividualQuery-138",
            "reason": "Geometry reference has no proven producing feature",
            "identifierCount": 1,
        }
    ]
    assert fillet["completeness"]["absence_proves_independence"] is False
    assert all(row["featureId"] != "sketch-1" for row in fillet["upstream"])


def test_dependency_slices_are_directional_bounded_deterministic_and_cycle_safe(
    realistic_payload: dict[str, object],
) -> None:
    index = FeatureIndex.build(realistic_payload)

    downstream = index.dependency_slice("sketch-1", direction="downstream", depth=2)
    upstream = index.dependency_slice("pattern-1", direction="upstream", depth=2)
    both_cycle = index.dependency_slice("cycle-a", direction="both", depth=3)
    node_limited = index.dependency_slice(
        "sketch-1", direction="downstream", depth=3, max_nodes=2, max_edges=80
    )
    edge_limited = index.dependency_slice(
        "sketch-1", direction="downstream", depth=3, max_nodes=80, max_edges=1
    )

    assert [node["featureId"] for node in downstream["nodes"]] == [
        "sketch-1",
        "extrude-1",
        "pattern-1",
    ]
    assert [node["featureId"] for node in upstream["nodes"]] == [
        "sketch-1",
        "extrude-1",
        "pattern-1",
    ]
    assert [node["featureId"] for node in both_cycle["nodes"]] == ["cycle-a", "cycle-b"]
    assert len(both_cycle["edges"]) == 2
    assert node_limited["truncated"] is True
    assert len(node_limited["nodes"]) == 2
    assert edge_limited["truncated"] is True
    assert len(edge_limited["edges"]) == 1
    assert downstream == index.dependency_slice("sketch-1", direction="downstream", depth=2)
