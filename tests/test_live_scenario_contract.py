"""Offline contracts for the local-only live acceptance predicates."""

from datetime import datetime, timezone
from inspect import signature

from tests.live.test_live_create_document import (
    _create_document_canary_name,
    test_live_create_document_canary as _live_create_document_canary,
)
from tests.live.test_live_deep_read import (
    _has_useful_normalized_parameters,
    _select_parameterized_ok_feature,
)


def test_create_document_canary_name_is_unique_and_bounded() -> None:
    instant = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)

    first = _create_document_canary_name(instant, "abcdef12")
    second = _create_document_canary_name(instant, "12345678")

    assert first == "Jarvis create_document canary 20260925T120000Z abcdef12"
    assert second != first
    assert len(first) <= 128


def test_create_document_canary_is_gated_bounded_and_owns_its_target() -> None:
    marks = {
        mark.name: mark
        for mark in getattr(_live_create_document_canary, "pytestmark", [])
    }

    assert {
        "live_onshape",
        "live_mutation",
        "live_create_document",
        "live_budget",
    } <= marks.keys()
    assert marks["live_budget"].args == (5,)
    assert "live_model_ids" not in signature(_live_create_document_canary).parameters


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
                        "parameterId": "unsupported",
                        "parameterType": "BTMParameterUnknown-999",
                        "value_summary": "unsupported parameter type",
                        "raw_available": True,
                    }
                ],
            }
        )
        is False
    )
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
