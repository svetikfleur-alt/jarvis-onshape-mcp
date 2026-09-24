"""Contracts for the native translation-only Transform payload."""

import importlib
import importlib.util

import pytest


def _transform_builder():
    spec = importlib.util.find_spec("onshape_mcp.builders.transform")
    assert spec is not None, "TransformBuilder module must exist"
    return importlib.import_module("onshape_mcp.builders.transform").TransformBuilder


def test_build_emits_exact_world_translation_payload_without_copy():
    transform_builder = _transform_builder()

    payload = (
        transform_builder(
            name="Shift bracket",
            translation_x="10 mm",
            translation_y="-0.5 in",
            translation_z=2,
        )
        .add_body("BODY-A")
        .add_body("BODY-B")
        .build()
    )

    assert payload == {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "transform",
            "name": "Shift bracket",
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "deterministicIds": ["BODY-A", "BODY-B"],
                        }
                    ],
                    "parameterId": "entities",
                },
                {
                    "btType": "BTMParameterEnum-145",
                    "enumName": "TransformType",
                    "value": "TRANSLATION_3D",
                    "parameterId": "transformType",
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "expression": "10 mm",
                    "parameterId": "dx",
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "expression": "-0.5 in",
                    "parameterId": "dy",
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "expression": "2 mm",
                    "parameterId": "dz",
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "value": False,
                    "parameterId": "makeCopy",
                },
            ],
        },
    }
    assert "libraryRelationType" not in str(payload)


def test_build_rejects_zero_motion():
    transform_builder = _transform_builder()
    builder = transform_builder().add_body("BODY-A")

    with pytest.raises(ValueError, match="translation must move at least one axis"):
        builder.build()


def test_build_requires_at_least_one_body():
    transform_builder = _transform_builder()

    with pytest.raises(ValueError, match="At least one body"):
        transform_builder(translation_x=1).build()


def test_add_body_rejects_blank_identifier():
    transform_builder = _transform_builder()

    with pytest.raises(ValueError, match="body_id must be a non-empty string"):
        transform_builder(translation_x=1).add_body("")
