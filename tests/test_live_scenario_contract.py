"""Offline contracts for the local-only live acceptance predicates."""

from tests.live.test_live_deep_read import (
    _has_useful_normalized_parameters,
    _select_parameterized_ok_feature,
)


def test_select_parameterized_ok_feature_skips_empty_and_non_ok_rows() -> None:
    expected = {
        "featureId": "feature-with-parameters",
        "status": "OK",
        "parameterCount": 2,
    }
    rows = [
        {"featureId": "empty", "status": "OK", "parameterCount": 0},
        {"featureId": "warning", "status": "WARNING", "parameterCount": 4},
        expected,
    ]

    assert _select_parameterized_ok_feature(rows) is expected
    assert _select_parameterized_ok_feature(rows[:2]) is None


def test_useful_normalized_parameters_requires_coherent_bounded_projection() -> None:
    useful = {
        "parameter_count": 2,
        "returned_parameters": 2,
        "parameters": [
            {
                "parameterId": "depth",
                "parameterType": "BTMParameterQuantity-147",
                "value_summary": {"expression": "10 mm"},
            },
            {
                "parameterId": "entities",
                "parameterType": "BTMParameterQueryList-148",
                "value_summary": {"query_count": 1},
                "reference_summary": {"confirmed": [], "unresolved_count": 1},
            },
        ],
    }

    assert _has_useful_normalized_parameters(useful) is True
    assert _has_useful_normalized_parameters({**useful, "returned_parameters": 1}) is False
    assert _has_useful_normalized_parameters({**useful, "parameters": []}) is False
    assert (
        _has_useful_normalized_parameters(
            {
                "parameter_count": 1,
                "returned_parameters": 1,
                "parameters": [
                    {
                        "parameterId": "depth",
                        "parameterType": "BTMParameterQuantity-147",
                    }
                ],
            }
        )
        is False
    )
