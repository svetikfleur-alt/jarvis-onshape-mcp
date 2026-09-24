"""Contracts for the native neutral-plane Draft payload."""

import importlib
import importlib.util

import pytest


def _draft_builder():
    spec = importlib.util.find_spec("onshape_mcp.builders.draft")
    assert spec is not None, "DraftBuilder module must exist"
    return importlib.import_module("onshape_mcp.builders.draft").DraftBuilder


def test_build_emits_exact_neutral_plane_payload():
    draft_builder = _draft_builder()

    payload = (
        draft_builder(
            name="Release draft",
            neutral_plane_id="PLANE-A",
            angle="3 deg",
            reverse_pull_direction=True,
        )
        .add_face("FACE-A")
        .add_face("FACE-B")
        .build()
    )

    assert payload == {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "draft",
            "name": "Release draft",
            "parameters": [
                {
                    "btType": "BTMParameterEnum-145",
                    "enumName": "DraftFeatureType",
                    "value": "NEUTRAL_PLANE",
                    "parameterId": "draftFeatureType",
                },
                {
                    "btType": "BTMParameterQueryList-148",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "deterministicIds": ["PLANE-A"],
                        }
                    ],
                    "parameterId": "neutralPlane",
                },
                {
                    "btType": "BTMParameterQueryList-148",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "deterministicIds": ["FACE-A", "FACE-B"],
                        }
                    ],
                    "parameterId": "draftFaces",
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "expression": "3 deg",
                    "parameterId": "angle",
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "value": True,
                    "parameterId": "pullDirection",
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "value": False,
                    "parameterId": "tangentPropagation",
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "value": False,
                    "parameterId": "reFillet",
                },
            ],
        },
    }
    assert "libraryRelationType" not in str(payload)


@pytest.mark.parametrize("angle", [0, -1, "89.9 deg", "2 rad"])
def test_build_rejects_angles_outside_neutral_plane_range(angle):
    draft_builder = _draft_builder()
    builder = draft_builder(neutral_plane_id="PLANE-A", angle=angle).add_face("FACE-A")

    with pytest.raises(ValueError, match="greater than 0 and less than 89.9 degrees"):
        builder.build()


def test_build_requires_neutral_plane_and_faces():
    draft_builder = _draft_builder()

    with pytest.raises(ValueError, match="neutral_plane_id is required"):
        draft_builder().add_face("FACE-A").build()
    with pytest.raises(ValueError, match="At least one draft face"):
        draft_builder(neutral_plane_id="PLANE-A").build()


def test_add_face_rejects_blank_identifier():
    draft_builder = _draft_builder()

    with pytest.raises(ValueError, match="face_id must be a non-empty string"):
        draft_builder(neutral_plane_id="PLANE-A").add_face("  ")
