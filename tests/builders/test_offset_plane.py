"""Behavior tests for offset construction-plane payloads."""

import pytest

from onshape_mcp.builders.offset_plane import OffsetPlaneBuilder


def _parameter(payload, parameter_id):
    return next(
        parameter
        for parameter in payload["feature"]["parameters"]
        if parameter["parameterId"] == parameter_id
    )


def test_build_requires_a_reference_before_emitting_a_payload():
    with pytest.raises(ValueError, match="reference_id is required"):
        OffsetPlaneBuilder().build()


def test_literal_offset_and_flip_are_encoded_for_onshape():
    builder = OffsetPlaneBuilder(name="Case split", flip=True)

    payload = builder.set_reference("FACE-A").set_offset("0.25 in").build()

    assert payload["feature"]["featureType"] == "cPlane"
    assert payload["feature"]["name"] == "Case split"
    assert _parameter(payload, "entities")["queries"][0]["deterministicIds"] == [
        "FACE-A"
    ]
    assert _parameter(payload, "offset") == {
        "btType": "BTMParameterQuantity-147",
        "isInteger": False,
        "value": pytest.approx(0.00635),
        "units": "",
        "expression": "0.25 in",
        "parameterId": "offset",
        "parameterName": "",
    }
    assert _parameter(payload, "oppositeDirection")["value"] is True


def test_variable_offset_uses_expression_without_parsing_placeholder_value():
    payload = (
        OffsetPlaneBuilder(reference_id="Top", offset="not a length")
        .set_offset("still ignored", variable_name="case_height")
        .build()
    )

    offset = _parameter(payload, "offset")
    assert offset["expression"] == "#case_height"
    assert offset["value"] == 0.0
