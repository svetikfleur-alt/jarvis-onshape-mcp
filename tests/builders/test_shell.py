"""Behavior tests for shell feature payloads."""

import pytest

from onshape_mcp.builders.shell import ShellBuilder


def _parameter(payload, parameter_id):
    return next(
        parameter
        for parameter in payload["feature"]["parameters"]
        if parameter["parameterId"] == parameter_id
    )


def test_build_requires_at_least_one_removed_face():
    with pytest.raises(ValueError, match="At least one face"):
        ShellBuilder().build()


def test_literal_thickness_preserves_all_face_ids_and_direction():
    builder = ShellBuilder(name="Open enclosure", thickness="2 mm", outward=True)

    payload = builder.add_face("TOP").add_face("PORT").build()

    assert payload["feature"]["featureType"] == "shell"
    assert payload["feature"]["name"] == "Open enclosure"
    assert _parameter(payload, "entities")["queries"][0]["deterministicIds"] == [
        "TOP",
        "PORT",
    ]
    thickness = _parameter(payload, "thickness")
    assert thickness["expression"] == "2 mm"
    assert thickness["value"] == pytest.approx(0.002)
    assert _parameter(payload, "oppositeDirection")["value"] is True


def test_variable_thickness_skips_literal_parsing():
    payload = (
        ShellBuilder(thickness="not a length")
        .add_face("TOP")
        .set_thickness("still ignored", variable_name="wall")
        .build()
    )

    thickness = _parameter(payload, "thickness")
    assert thickness["expression"] == "#wall"
    assert thickness["value"] == 0.0
