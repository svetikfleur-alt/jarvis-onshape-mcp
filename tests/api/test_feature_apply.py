"""Unit tests for update_feature_params_and_check.

apply_feature_and_check itself is covered end-to-end by
tests/real/test_feature_apply_real.py; these tests pin the
param-merge behavior of the update helper without hitting Onshape.
"""

import copy
from unittest.mock import AsyncMock

import httpx
import pytest

from onshape_mcp.api.feature_apply import (
    apply_feature_and_check,
    apply_assembly_feature_and_check,
    delete_partstudio_feature_and_check,
    update_feature_params_and_check,
    FeatureApplyResult,
)
from onshape_mcp.api.custom_features import CustomFeatureManager
from onshape_mcp.api.sketch_edit import edit_sketch


class TestApplyAssemblyFeatureAndCheck:
    """Pin the contract of the assembly-side wire-truth helper."""

    @pytest.mark.asyncio
    async def test_posts_to_assemblies_path(self, onshape_client):
        onshape_client.post = AsyncMock(return_value={
            "feature": {"featureId": "f1", "name": "MC", "featureType": "mateConnector"},
            "featureState": {"featureStatus": "OK"},
        })
        result = await apply_assembly_feature_and_check(
            onshape_client, "docA", "wsA", "asmA", {"feature": {}},
        )
        assert isinstance(result, FeatureApplyResult)
        assert result.status == "OK"
        assert result.feature_id == "f1"
        assert result.feature_type == "mateConnector"
        # Path targets the assemblies endpoint.
        posted_path = onshape_client.post.await_args[0][0]
        assert "/api/v9/assemblies/d/docA/w/wsA/e/asmA/features" in posted_path

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["OK", "INFO"])
    async def test_response_status_does_not_verify_requested_assembly_state(
        self, onshape_client, status
    ):
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {
                    "featureId": "f1",
                    "name": "MC",
                    "featureType": "mateConnector",
                },
                "featureState": {"featureStatus": status},
            }
        )
        onshape_client.get = AsyncMock()

        result = await apply_assembly_feature_and_check(
            onshape_client,
            "docA",
            "wsA",
            "asmA",
            {"feature": {"name": "MC", "featureType": "mateConnector"}},
        )

        assert result.ok is False
        assert result.regen_ok is True
        assert result.mutation_verification == "unverified"
        assert result.changed is None
        assert result.verification_scope == "response_feature_state"
        assert result.reason_code == "REQUESTED_STATE_UNVERIFIED"
        onshape_client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_error_status_surfaces_as_ok_false(self, onshape_client):
        """Onshape-reported featureStatus=ERROR becomes ok=False with message."""
        onshape_client.post = AsyncMock(return_value={
            "feature": {"featureId": "badMate", "name": "m", "featureType": "mate"},
            "featureState": {
                "featureStatus": "ERROR",
                "message": "Solver rejected: over-constrained",
            },
        })
        result = await apply_assembly_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": {}},
        )
        assert result.ok is False
        assert result.status == "ERROR"
        assert result.regen_ok is False
        assert result.mutation_verification == "failed"
        assert "over-constrained" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_missing_assembly_status_is_unverified(self, onshape_client):
        onshape_client.post = AsyncMock(
            return_value={"feature": {"featureId": "unknown"}}
        )
        onshape_client.get = AsyncMock(return_value={"featureStates": {}})

        result = await apply_assembly_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": {"name": "Requested"}}
        )

        assert result.ok is False
        assert result.regen_ok is None
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "FEATURE_STATUS_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_update_operation_uses_featureid_path(self, onshape_client):
        onshape_client.post = AsyncMock(return_value={
            "feature": {"featureId": "fixed"},
            "featureState": {"featureStatus": "OK"},
        })
        await apply_assembly_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": {}},
            operation="update", feature_id="fixed",
        )
        posted_path = onshape_client.post.await_args[0][0]
        assert posted_path.endswith("/features/featureid/fixed")

    @pytest.mark.asyncio
    async def test_update_without_feature_id_rejects(self, onshape_client):
        with pytest.raises(ValueError):
            await apply_assembly_feature_and_check(
                onshape_client, "d", "w", "e", {"feature": {}},
                operation="update",
            )


def _extrude_feature(
    feature_id: str = "fId",
    depth_expr: str = "10 mm",
    depth_value: float = 0.01,
) -> dict:
    return {
        "featureId": feature_id,
        "name": "Extrude 10mm",
        "featureType": "extrude",
        "parameters": [
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "depth",
                "expression": depth_expr,
                "value": depth_value,
                "units": "meter",
            },
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "oppositeDirection",
                "value": False,
            },
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "operationType",
                "value": "NEW",
            },
        ],
    }


@pytest.mark.asyncio
async def test_update_merges_expression_and_clears_numeric(onshape_client):
    """Quantity update with only `expression` must zero stale numeric value."""
    onshape_client.get = AsyncMock(
        side_effect=[
            {"features": [_extrude_feature(depth_expr="10 mm", depth_value=0.01)]},
            {
                "features": [_extrude_feature(depth_expr="15 mm", depth_value=0.015)],
                "featureStates": {"fId": {"featureStatus": "OK"}},
            },
        ]
    )
    onshape_client.post = AsyncMock(
        return_value={
            "feature": {"featureId": "fId", "name": "Extrude 10mm", "featureType": "extrude"},
            "featureState": {"featureStatus": "OK"},
        }
    )

    result = await update_feature_params_and_check(
        onshape_client, "d", "w", "e", "fId",
        [{"parameterId": "depth", "expression": "15 mm"}],
    )

    assert isinstance(result, FeatureApplyResult)
    assert result.ok is True
    sent_payload = onshape_client.post.await_args[1]["data"]
    depth_param = next(
        p for p in sent_payload["feature"]["parameters"]
        if p["parameterId"] == "depth"
    )
    assert depth_param["expression"] == "15 mm"
    # Stale numeric cleared so Onshape re-evaluates.
    assert depth_param["value"] == 0.0
    # Other params untouched.
    assert sent_payload["feature"]["parameters"][1]["value"] is False


@pytest.mark.asyncio
async def test_update_preserves_explicit_value(onshape_client):
    """If the caller passes `value` along with `expression`, keep both as given."""
    onshape_client.get = AsyncMock(
        return_value={"features": [_extrude_feature(depth_expr="10 mm", depth_value=0.01)]}
    )
    onshape_client.post = AsyncMock(
        return_value={
            "feature": {"featureId": "fId"},
            "featureState": {"featureStatus": "OK"},
        }
    )

    await update_feature_params_and_check(
        onshape_client, "d", "w", "e", "fId",
        [{"parameterId": "depth", "expression": "20 mm", "value": 0.02}],
    )
    sent = onshape_client.post.await_args[1]["data"]
    depth = next(p for p in sent["feature"]["parameters"] if p["parameterId"] == "depth")
    assert depth["expression"] == "20 mm"
    assert depth["value"] == 0.02


@pytest.mark.asyncio
async def test_update_boolean_and_enum(onshape_client):
    """Non-quantity updates write `value` straight through."""
    onshape_client.get = AsyncMock(
        return_value={"features": [_extrude_feature()]}
    )
    onshape_client.post = AsyncMock(
        return_value={
            "feature": {"featureId": "fId"},
            "featureState": {"featureStatus": "OK"},
        }
    )

    await update_feature_params_and_check(
        onshape_client, "d", "w", "e", "fId",
        [
            {"parameterId": "oppositeDirection", "value": True},
            {"parameterId": "operationType", "value": "ADD"},
        ],
    )
    sent = onshape_client.post.await_args[1]["data"]
    by_id = {p["parameterId"]: p for p in sent["feature"]["parameters"]}
    assert by_id["oppositeDirection"]["value"] is True
    assert by_id["operationType"]["value"] == "ADD"
    # depth expression left alone.
    assert by_id["depth"]["expression"] == "10 mm"


@pytest.mark.asyncio
async def test_update_hits_update_path(onshape_client):
    """POST must go to the featureid update path, not the list path."""
    onshape_client.get = AsyncMock(return_value={"features": [_extrude_feature()]})
    onshape_client.post = AsyncMock(
        return_value={
            "feature": {"featureId": "fId"},
            "featureState": {"featureStatus": "OK"},
        }
    )

    await update_feature_params_and_check(
        onshape_client, "d", "w", "e", "fId",
        [{"parameterId": "depth", "expression": "15 mm"}],
    )
    posted_path = onshape_client.post.await_args[0][0]
    assert posted_path.endswith("/features/featureid/fId")


@pytest.mark.asyncio
async def test_update_raises_for_unknown_feature(onshape_client):
    onshape_client.get = AsyncMock(return_value={"features": []})
    onshape_client.post = AsyncMock()
    with pytest.raises(ValueError) as exc:
        await update_feature_params_and_check(
            onshape_client, "d", "w", "e", "missing",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )
    assert "not found" in str(exc.value)
    onshape_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_update_raises_for_unknown_parameter(onshape_client):
    """parameterId that doesn't exist on the feature is a driver error."""
    onshape_client.get = AsyncMock(return_value={"features": [_extrude_feature()]})
    onshape_client.post = AsyncMock()
    with pytest.raises(ValueError) as exc:
        await update_feature_params_and_check(
            onshape_client, "d", "w", "e", "fId",
            [{"parameterId": "nope", "expression": "15 mm"}],
        )
    assert "nope" in str(exc.value)
    onshape_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_assign_variable_rejects_value_slot_before_post(onshape_client):
    """Issue #6: LENGTH assignVariable uses its current feature-defined slot."""
    assign_variable = {
        "btType": "BTMFeature-134",
        "featureId": "variable-feature",
        "featureType": "assignVariable",
        "name": "Variable 1",
        "parameters": [
            {
                "btType": "BTMParameterString-149",
                "parameterId": "variableName",
                "value": "length",
            },
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "lengthValue",
                "expression": "10 mm",
                "value": 0.01,
            },
        ],
    }
    onshape_client.get = AsyncMock(return_value={"features": [assign_variable]})
    onshape_client.post = AsyncMock()

    with pytest.raises(ValueError, match="parameterId.*value"):
        await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "variable-feature",
            [{"parameterId": "value", "expression": "15 mm"}],
        )

    onshape_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_rejects_missing_fields(onshape_client):
    onshape_client.get = AsyncMock(return_value={"features": [_extrude_feature()]})
    onshape_client.post = AsyncMock()

    with pytest.raises(ValueError):
        await update_feature_params_and_check(
            onshape_client, "d", "w", "e", "fId", [{"expression": "15 mm"}],
        )
    with pytest.raises(ValueError):
        await update_feature_params_and_check(
            onshape_client, "d", "w", "e", "fId", [],
        )
    with pytest.raises(ValueError):
        await update_feature_params_and_check(
            onshape_client, "d", "w", "e", "", [{"parameterId": "depth"}],
        )
    onshape_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_update_reports_post_error_status(onshape_client):
    """If the post-patch featureStatus is ERROR, ok=False bubbles through."""
    onshape_client.get = AsyncMock(return_value={"features": [_extrude_feature()]})
    onshape_client.post = AsyncMock(
        return_value={
            "feature": {"featureId": "fId"},
            "featureState": {
                "featureStatus": "ERROR",
                "message": "Depth must be positive",
            },
        }
    )

    result = await update_feature_params_and_check(
        onshape_client, "d", "w", "e", "fId",
        [{"parameterId": "depth", "expression": "-15 mm"}],
    )
    assert result.ok is False
    assert result.status == "ERROR"
    assert "positive" in (result.error_message or "")


class TestPartStudioMutationTruth:
    @pytest.mark.parametrize(
        "verification", ["unverified", "no_effect", "failed"]
    )
    def test_public_projection_refuses_nonverified_ok_claim(self, verification):
        result = FeatureApplyResult(
            ok=True,
            status="OK",
            feature_id="unsafe",
            feature_name="Unverified",
            feature_type="extrude",
            transport_ok=True,
            http_ok=True,
            regen_ok=True,
            mutation_verification=verification,
        )

        assert result.public_dict()["ok"] is False

    @pytest.mark.asyncio
    async def test_create_requires_authoritative_reread_for_verified_success(
        self, onshape_client
    ):
        requested = _extrude_feature(feature_id="")
        requested.pop("featureId")
        response_feature = _extrude_feature(feature_id="created")
        onshape_client.post = AsyncMock(
            return_value={
                "feature": response_feature,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [response_feature],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is True
        assert result.transport_ok is True
        assert result.http_ok is True
        assert result.regen_ok is True
        assert result.mutation_verification == "verified"
        assert result.changed is True
        assert result.verification_scope == "feature_state"
        onshape_client.get.assert_awaited_once_with(
            "/api/v9/partstudios/d/d/w/w/e/e/features",
            params={"featureId": ["created"]},
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("feature_type", "requested_parameters", "authoritative_parameters"),
        [
            (
                "extrude",
                [
                    {
                        "btType": "BTMParameterQuantity-147",
                        "parameterId": "depth",
                        "parameterName": "",
                        "expression": "10 mm",
                        "value": 0.01,
                        "units": "",
                    }
                ],
                [
                    {
                        "btType": "BTMParameterQuantity-147",
                        "parameterId": "depth",
                        "expression": "0.01 m",
                        "value": 0.01,
                        "units": "meter",
                        "nodeId": "server-node",
                    }
                ],
            ),
            (
                "linearPattern",
                [
                    {
                        "btType": "BTMParameterFeatureList-1749",
                        "parameterId": "instanceFunction",
                        "featureIds": ["feature-b", "feature-a"],
                        "parameterName": "",
                    }
                ],
                [
                    {
                        "btType": "BTMParameterFeatureList-1749",
                        "parameterId": "instanceFunction",
                        "featureIds": ["feature-a", "feature-b"],
                    }
                ],
            ),
            (
                "shell",
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "entities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["face-b", "face-a"],
                            }
                        ],
                    }
                ],
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "entities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["face-a", "face-b"],
                                "queryString": "server canonical query",
                            }
                        ],
                    }
                ],
            ),
            (
                "chamfer",
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "entities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["edge-b", "edge-a"],
                            }
                        ],
                    }
                ],
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "entities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["edge-a", "edge-b"],
                            }
                        ],
                    }
                ],
            ),
            (
                "draft",
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "draftEntities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["face-b", "face-a"],
                            }
                        ],
                    }
                ],
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "draftEntities",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["face-a", "face-b"],
                            }
                        ],
                    }
                ],
            ),
            (
                "transform",
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "bodies",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["body-b", "body-a"],
                            }
                        ],
                    }
                ],
                [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "parameterId": "bodies",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": ["body-a", "body-b"],
                            }
                        ],
                    }
                ],
            ),
        ],
    )
    async def test_common_capability_canonical_state_verifies_without_agent_followup(
        self,
        onshape_client,
        feature_type,
        requested_parameters,
        authoritative_parameters,
    ):
        requested = {
            "btType": "BTMFeature-134",
            "featureType": feature_type,
            "name": "Requested feature",
            "suppressed": False,
            "namespace": "",
            "parameters": requested_parameters,
        }
        actual = {
            "btType": "BTMFeature-999",
            "featureId": "created",
            "featureType": feature_type,
            "name": "Requested feature",
            "suppressed": False,
            "parameters": authoritative_parameters,
            "nodeId": "server-feature-node",
        }
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [actual],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is True
        assert result.mutation_verification == "verified"
        assert result.reason_code == "REQUESTED_STATE_VERIFIED"

    @pytest.mark.asyncio
    async def test_create_with_failed_reread_is_honestly_unverified(
        self, onshape_client
    ):
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "created", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(side_effect=RuntimeError("reread unavailable"))

        result = await apply_feature_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            {"feature": {"featureType": "extrude", "parameters": []}},
        )

        assert result.ok is False
        assert result.transport_ok is True
        assert result.regen_ok is True
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "AUTHORITATIVE_REREAD_FAILED"

    @pytest.mark.asyncio
    async def test_create_without_returned_feature_id_is_unverified(
        self, onshape_client
    ):
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [_extrude_feature(feature_id="server-created")],
                "featureStates": {
                    "server-created": {"featureStatus": "OK"}
                },
            }
        )

        result = await apply_feature_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            {"feature": {"featureType": "extrude", "parameters": []}},
        )

        assert result.ok is False
        assert result.regen_ok is None
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "FEATURE_ID_UNAVAILABLE"
        onshape_client.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_without_authoritative_feature_status_is_unverified(
        self, onshape_client
    ):
        requested = _extrude_feature(feature_id="")
        requested.pop("featureId")
        actual = _extrude_feature(feature_id="created")
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(return_value={"features": [actual]})

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is False
        assert result.status == "UNKNOWN"
        assert result.regen_ok is None
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "FEATURE_STATUS_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_create_without_substantive_requested_fields_is_unverified(
        self, onshape_client
    ):
        actual = _extrude_feature(feature_id="created")
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [actual],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": {}}
        )

        assert result.ok is False
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "REQUESTED_STATE_UNVERIFIED"

    @pytest.mark.asyncio
    async def test_create_canonicalized_quantity_expression_is_verified_by_value(
        self, onshape_client
    ):
        requested = _extrude_feature(feature_id="", depth_expr="10 mm")
        requested.pop("featureId")
        actual = _extrude_feature(feature_id="created", depth_expr="0.01 m")
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [actual],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is True
        assert result.regen_ok is True
        assert result.mutation_verification == "verified"
        assert result.reason_code == "REQUESTED_STATE_VERIFIED"

    @pytest.mark.asyncio
    async def test_create_canonicalized_query_text_is_verified_by_feature_reference(
        self, onshape_client
    ):
        requested = _extrude_feature(feature_id="")
        requested.pop("featureId")
        requested["parameters"].append(
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "entities",
                "queries": [
                    {
                        "btType": "BTMIndividualSketchRegionQuery-140",
                        "queryStatement": None,
                        "queryString": 'query = qSketchRegion(id + "sketch-1", true);',
                        "featureId": "sketch-1",
                        "filterInnerLoops": True,
                        "deterministicIds": [],
                    }
                ],
            }
        )
        actual = _extrude_feature(feature_id="created")
        actual["parameters"].append(
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "entities",
                "queries": [
                    {
                        "btType": "BTMIndividualSketchRegionQuery-140",
                        "queryStatement": "qSketchRegion(sketch-1)",
                        "queryString": "canonicalized by Onshape",
                        "featureId": "sketch-1",
                        "filterInnerLoops": True,
                        "deterministicIds": [],
                    }
                ],
            }
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [actual],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is True
        assert result.mutation_verification == "verified"
        assert result.reason_code == "REQUESTED_STATE_VERIFIED"

    @pytest.mark.asyncio
    async def test_create_query_reference_mismatch_is_failed(
        self, onshape_client
    ):
        requested = _extrude_feature(feature_id="")
        requested.pop("featureId")
        requested["parameters"].append(
            {
                "btType": "BTMParameterQueryList-148",
                "parameterId": "entities",
                "queries": [
                    {
                        "btType": "BTMIndividualSketchRegionQuery-140",
                        "queryStatement": None,
                        "queryString": 'query = qSketchRegion(id + "sketch-1", true);',
                        "featureId": "sketch-1",
                        "filterInnerLoops": True,
                        "deterministicIds": [],
                    }
                ],
            }
        )
        actual = copy.deepcopy(requested)
        actual["featureId"] = "created"
        entities = next(
            parameter
            for parameter in actual["parameters"]
            if parameter["parameterId"] == "entities"
        )
        entities["queries"][0]["featureId"] = "different-sketch"
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [actual],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is False
        assert result.mutation_verification == "failed"
        assert result.reason_code == "REQUESTED_STATE_MISMATCH"

    @pytest.mark.asyncio
    async def test_regeneration_error_is_failed_even_when_http_succeeds(
        self, onshape_client
    ):
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "bad", "featureType": "extrude"},
                "featureState": {
                    "featureStatus": "ERROR",
                    "message": "Failed to regenerate",
                },
            }
        )

        result = await apply_feature_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            {"feature": {"featureType": "extrude", "parameters": []}},
        )

        assert result.ok is False
        assert result.transport_ok is True
        assert result.http_ok is True
        assert result.regen_ok is False
        assert result.mutation_verification == "failed"
        assert result.reason_code == "FEATURE_REGENERATION_ERROR"

    @pytest.mark.asyncio
    async def test_update_reread_verifies_only_requested_parameter_fields(
        self, onshape_client
    ):
        before = _extrude_feature(depth_expr="10 mm", depth_value=0.01)
        after = _extrude_feature(depth_expr="15 mm", depth_value=0.015)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )

        assert result.ok is True
        assert result.mutation_verification == "verified"
        assert result.changed is True
        assert result.verification_scope == "requested_parameters"
        assert onshape_client.get.await_count == 2
        assert onshape_client.get.await_args_list[0].kwargs == {
            "params": {"featureId": ["fId"]}
        }
        assert onshape_client.get.await_args_list[1].kwargs == {
            "params": {"featureId": ["fId"]}
        }

    @pytest.mark.asyncio
    async def test_update_canonical_quantity_value_verifies_new_state(
        self, onshape_client
    ):
        before = _extrude_feature(depth_expr="10 mm", depth_value=0.01)
        after = _extrude_feature(depth_expr="0.015 m", depth_value=0.015)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )

        assert result.ok is True
        assert result.mutation_verification == "verified"
        assert result.reason_code == "REQUESTED_STATE_VERIFIED"

    @pytest.mark.asyncio
    async def test_update_canonical_quantity_old_value_does_not_verify(
        self, onshape_client
    ):
        before = _extrude_feature(depth_expr="10 mm", depth_value=0.01)
        after = _extrude_feature(depth_expr="0.01 m", depth_value=0.01)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )

        assert result.ok is False
        assert result.mutation_verification == "no_effect"
        assert result.reason_code == "REQUESTED_STATE_UNCHANGED"

    @pytest.mark.asyncio
    async def test_update_checks_fields_after_canonical_expression_match(
        self, onshape_client
    ):
        before = _extrude_feature(depth_expr="10 mm", depth_value=0.01)
        after = _extrude_feature(depth_expr="0.015 m", depth_value=0.015)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [
                {
                    "parameterId": "depth",
                    "expression": "15 mm",
                    "value": 0.02,
                }
            ],
        )

        assert result.ok is False
        assert result.mutation_verification == "failed"
        assert result.reason_code == "REQUESTED_STATE_MISMATCH"

    @pytest.mark.asyncio
    async def test_empty_deterministic_ids_do_not_prove_query_text_equivalence(
        self, onshape_client
    ):
        requested = {
            "featureType": "revolve",
            "name": "Revolve",
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "axis",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "deterministicIds": [],
                            "queryString": "qCreatedBy(makeId('Top'), EntityType.EDGE)",
                        }
                    ],
                }
            ],
        }
        actual = copy.deepcopy(requested)
        actual["featureId"] = "created"
        actual["parameters"][0]["queries"][0]["queryString"] = (
            "qCreatedBy(makeId('Right'), EntityType.EDGE)"
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": actual,
                "featureState": {"featureStatus": "OK"},
            }
        )
        onshape_client.get = AsyncMock(
            return_value={
                "features": [actual],
                "featureStates": {"created": {"featureStatus": "OK"}},
            }
        )

        result = await apply_feature_and_check(
            onshape_client, "d", "w", "e", {"feature": requested}
        )

        assert result.ok is False
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "REQUESTED_STATE_UNVERIFIED"

    @pytest.mark.asyncio
    async def test_literal_update_does_not_treat_equal_valued_variable_as_satisfied(
        self, onshape_client
    ):
        before = _extrude_feature(depth_expr="#depth", depth_value=0.015)
        after = _extrude_feature(depth_expr="15 mm", depth_value=0.015)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )

        assert result.ok is True
        assert result.mutation_verification == "verified"
        onshape_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_equal_numeric_values_with_different_quantity_kinds_do_not_verify(
        self, onshape_client
    ):
        before = _extrude_feature(depth_expr="10 mm", depth_value=0.01)
        after = _extrude_feature(depth_expr="1 m", depth_value=1.0)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "1 rad"}],
        )

        assert result.ok is False
        assert result.mutation_verification == "failed"

    @pytest.mark.asyncio
    async def test_update_already_satisfied_is_no_effect_without_post(
        self, onshape_client
    ):
        onshape_client.get = AsyncMock(
            return_value={
                "features": [_extrude_feature(depth_expr="15 mm")],
                "featureStates": {"fId": {"featureStatus": "OK"}},
            }
        )
        onshape_client.post = AsyncMock()

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )

        assert result.ok is False
        assert result.transport_ok is None
        assert result.mutation_verification == "no_effect"
        assert result.changed is False
        assert result.reason_code == "REQUESTED_STATE_ALREADY_PRESENT"
        onshape_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_accepted_but_unchanged_is_no_effect(self, onshape_client):
        before = _extrude_feature(depth_expr="10 mm")
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [_extrude_feature(depth_expr="10 mm")],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "expression": "15 mm"}],
        )

        assert result.ok is False
        assert result.mutation_verification == "no_effect"
        assert result.changed is False
        assert result.reason_code == "REQUESTED_STATE_UNCHANGED"

    @pytest.mark.asyncio
    async def test_update_stable_value_mismatch_is_failed(self, onshape_client):
        before = _extrude_feature(depth_value=0.01)
        after = _extrude_feature(depth_value=0.03)
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "fId", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await update_feature_params_and_check(
            onshape_client,
            "d",
            "w",
            "e",
            "fId",
            [{"parameterId": "depth", "value": 0.02}],
        )

        assert result.ok is False
        assert result.mutation_verification == "failed"
        assert result.changed is True
        assert result.reason_code == "REQUESTED_STATE_MISMATCH"

    @pytest.mark.asyncio
    async def test_delete_is_verified_by_absence_and_regeneration_state(self):
        manager = AsyncMock()
        manager.get_features = AsyncMock(
            side_effect=[
                {
                    "features": [_extrude_feature()],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
                {"features": [], "featureStates": {}},
            ]
        )

        result = await delete_partstudio_feature_and_check(
            manager, "d", "w", "e", "fId"
        )

        assert result.ok is True
        assert result.regen_ok is True
        assert result.mutation_verification == "verified"
        assert result.reason_code == "FEATURE_ABSENCE_VERIFIED"
        manager.delete_feature.assert_awaited_once_with("d", "w", "e", "fId")

    @pytest.mark.asyncio
    async def test_delete_without_post_state_is_honestly_unverified(self):
        manager = AsyncMock()
        manager.get_features = AsyncMock(
            side_effect=[
                {
                    "features": [_extrude_feature()],
                    "featureStates": {"fId": {"featureStatus": "OK"}},
                },
                {"features": []},
            ]
        )

        result = await delete_partstudio_feature_and_check(
            manager, "d", "w", "e", "fId"
        )

        assert result.ok is False
        assert result.changed is True
        assert result.regen_ok is None
        assert result.mutation_verification == "unverified"
        assert result.reason_code == "REGENERATION_STATUS_UNAVAILABLE_AFTER_DELETE"

    def test_raw_is_internal_and_excluded_from_default_model_serialization(self):
        poisons = {
            "documentId": "PRIVATE_DOCUMENT_CANARY",
            "workspaceId": "PRIVATE_WORKSPACE_CANARY",
            "elementId": "PRIVATE_ELEMENT_CANARY",
            "arbitrary": "RAW_RESPONSE_CANARY",
        }
        result = FeatureApplyResult(
            ok=False,
            status="UNKNOWN",
            feature_id="",
            feature_name="",
            feature_type="",
            diagnostic=poisons,
            raw=poisons,
        )

        assert result.raw == poisons
        assert result.diagnostic == poisons
        assert "raw" not in result.model_dump()
        assert "diagnostic" not in result.model_dump()
        assert "raw" not in result.public_dict()
        assert "diagnostic" not in result.public_dict()
        rendered = result.model_dump_json()
        for poison in poisons.values():
            assert poison not in rendered


class TestHTTPMutationDiagnostics:
    @pytest.mark.asyncio
    async def test_http_400_preserves_bounded_structured_diagnostic(
        self, onshape_client, mock_httpx_client
    ):
        private_doc = "PRIVATE_DOCUMENT_CANARY"
        private_workspace = "PRIVATE_WORKSPACE_CANARY"
        private_element = "PRIVATE_ELEMENT_CANARY"
        arbitrary = '["RAW_RESPONSE_CANARY"]'
        path = (
            f"/api/v9/partstudios/d/{private_doc}/w/{private_workspace}"
            f"/e/{private_element}/features"
        )
        request = httpx.Request("POST", f"https://test.onshape.com{path}")
        response = httpx.Response(
            400,
            request=request,
            text=(
                "(was com.belmonttech.restapi.jackson."
                "BTWeirdStringValueException)\n"
                "(through reference chain:\n BTFeatureDefinitionCall[\"feature\"]"
                "->BTMFeature[\"parameters\"]->java.util.ArrayList[0])\n"
                f"{arbitrary}"
            ),
        )
        mock_httpx_client.post.return_value = response

        with pytest.raises(httpx.HTTPStatusError) as caught:
            await onshape_client.post(path, data={"feature": {"private": arbitrary}})

        diagnostic = caught.value.onshape_diagnostic
        assert diagnostic == {
            "failure_kind": "http_rejection",
            "method": "POST",
            "route": "/api/v9/partstudios/d/{id}/w/{id}/e/{id}/features",
            "status_code": 400,
            "category": "BTWeirdStringValueException",
            "reference_path": "feature.parameters[0]",
            "message": "Onshape rejected the request payload.",
        }
        rendered = str(caught.value)
        for poison in [private_doc, private_workspace, private_element, arbitrary]:
            assert poison not in rendered


class TestFeatureScriptVersionPreflight:
    @staticmethod
    def _source(*imports: str, version: str = "3029") -> str:
        return "\n".join([f"FeatureScript {version};", *imports, "export const x = 1;"])

    @pytest.mark.asyncio
    async def test_no_std_import_is_allowed_and_discovery_is_cached(
        self, onshape_client
    ):
        onshape_client.get = AsyncMock(
            return_value=[{"name": "Start"}, {"name": "3029.0"}]
        )
        manager = CustomFeatureManager(onshape_client)
        manager.create_feature_studio = AsyncMock(side_effect=RuntimeError("stop after preflight"))
        source = self._source(
            'import(path:"custom/workspace.fs",version:"abc123microversion");'
        )

        for _ in range(2):
            with pytest.raises(RuntimeError, match="stop after preflight"):
                await manager.apply_featurescript_feature(
                    "d",
                    "w",
                    "e",
                    feature_type="customFeature",
                    feature_script=source,
                    feature_name="Custom",
                )

        assert onshape_client.get.await_count == 1
        assert manager.create_feature_studio.await_count == 2

    @pytest.mark.asyncio
    async def test_only_std_import_version_is_compared(self, onshape_client):
        onshape_client.get = AsyncMock(return_value=[{"name": "3029.0"}])
        manager = CustomFeatureManager(onshape_client)
        manager.create_feature_studio = AsyncMock(side_effect=RuntimeError("preflight passed"))
        source = self._source(
            'import(path:"onshape/std/geometry.fs",version:"3029.0");',
            'import(path:"linked/custom.fs",version:"different-reference");',
        )

        with pytest.raises(RuntimeError, match="preflight passed"):
            await manager.apply_featurescript_feature(
                "d",
                "w",
                "e",
                feature_type="customFeature",
                feature_script=source,
                feature_name="Custom",
            )

        manager.create_feature_studio.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source",
        [
            _source.__func__(version="2909"),
            _source.__func__(
                'import(path:"onshape/std/geometry.fs",version:"2909.0");'
            ),
            _source.__func__('import(path:"onshape/std/geometry.fs");'),
            _source.__func__(
                'import(path:"onshape/std/geometry.fs",version:"3029.0")'
            ),
            "export const missingPrelude = true;",
        ],
    )
    async def test_stale_or_missing_std_version_fails_before_mutation(
        self, onshape_client, source
    ):
        onshape_client.get = AsyncMock(return_value=[{"name": "3029.0"}])
        manager = CustomFeatureManager(onshape_client)
        manager.create_feature_studio = AsyncMock()

        with pytest.raises(ValueError):
            await manager.apply_featurescript_feature(
                "d",
                "w",
                "e",
                feature_type="customFeature",
                feature_script=source,
                feature_name="Custom",
            )

        manager.create_feature_studio.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discovery_failure_fails_before_mutation(self, onshape_client):
        onshape_client.get = AsyncMock(side_effect=RuntimeError("discovery unavailable"))
        manager = CustomFeatureManager(onshape_client)
        manager.create_feature_studio = AsyncMock()

        with pytest.raises(RuntimeError, match="discovery unavailable"):
            await manager.apply_featurescript_feature(
                "d",
                "w",
                "e",
                feature_type="customFeature",
                feature_script=self._source(),
                feature_name="Custom",
            )

        manager.create_feature_studio.assert_not_awaited()


class TestSketchMutationTruth:
    @staticmethod
    def _sketch(entities=None, constraints=None):
        return {
            "featureId": "sketch1",
            "btType": "BTMSketch-151",
            "name": "Sketch",
            "entities": entities or [],
            "constraints": constraints or [],
        }

    @staticmethod
    def _horizontal_constraint(constraint_id: str, entity_ref: str):
        return {
            "btType": "BTMSketchConstraint-2",
            "namespace": "",
            "name": "",
            "helpParameters": [],
            "hasOffsetData1": False,
            "offsetOrientation1": False,
            "offsetDistance1": 0.0,
            "hasOffsetData2": False,
            "offsetOrientation2": False,
            "offsetDistance2": 0.0,
            "hasPierceParameter": False,
            "pierceParameter": 0.0,
            "index": 1,
            "constraintType": "HORIZONTAL",
            "parameters": [
                {
                    "btType": "BTMParameterString-149",
                    "value": entity_ref,
                    "parameterId": "localFirst",
                    "parameterName": "",
                }
            ],
            "entityId": constraint_id,
        }

    @pytest.mark.asyncio
    async def test_added_entity_requires_requested_fields_on_reread(
        self, onshape_client
    ):
        before = self._sketch()
        added = {
            "btType": "BTMSketchPoint-158",
            "entityId": "point2",
            "x": 1.0,
            "y": 2.0,
            "isConstruction": False,
        }
        after = self._sketch(entities=[{**added, "nodeId": "server-only"}])
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client,
            "d",
            "w",
            "e",
            "sketch1",
            add_entities=[{"type": "point", "id": "point2", "at": [1000, 2000]}],
        )

        assert result.apply.ok is True
        assert result.apply.mutation_verification == "verified"
        assert result.apply.verification_scope == "sketch_state"

    @pytest.mark.asyncio
    async def test_membership_without_comparable_geometry_is_unverified(
        self, onshape_client
    ):
        before = self._sketch()
        after = self._sketch(
            entities=[
                {
                    "btType": "BTMSketchPoint-158",
                    "entityId": "point2",
                    "x": 9.0,
                    "y": 2.0,
                    "isConstruction": False,
                }
            ]
        )
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client,
            "d",
            "w",
            "e",
            "sketch1",
            add_entities=[{"type": "point", "id": "point2", "at": [1000, 2000]}],
        )

        assert result.apply.ok is False
        assert result.apply.mutation_verification == "unverified"

    @pytest.mark.asyncio
    async def test_removed_entity_is_verified_by_absence(self, onshape_client):
        before = self._sketch(
            entities=[
                {
                    "btType": "BTMSketchPoint-158",
                    "entityId": "point1",
                    "x": 0.0,
                    "y": 0.0,
                    "isConstruction": False,
                }
            ]
        )
        after = self._sketch()
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client, "d", "w", "e", "sketch1", remove_ids=["point1"]
        )

        assert result.apply.ok is True
        assert result.apply.mutation_verification == "verified"

    @pytest.mark.asyncio
    async def test_unknown_remove_id_is_rejected_before_post(self, onshape_client):
        onshape_client.get = AsyncMock(
            return_value={"features": [self._sketch()]}
        )
        onshape_client.post = AsyncMock()

        with pytest.raises(ValueError, match="not present"):
            await edit_sketch(
                onshape_client,
                "d",
                "w",
                "e",
                "sketch1",
                remove_ids=["missing-id"],
            )

        onshape_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cascaded_constraint_removal_is_verified_by_absence(
        self, onshape_client
    ):
        point = {
            "btType": "BTMSketchPoint-158",
            "entityId": "point1",
            "x": 0.0,
            "y": 0.0,
            "isConstruction": False,
        }
        dependent = self._horizontal_constraint("c1", "point1")
        before = self._sketch(entities=[point], constraints=[dependent])
        # Simulate a server no-op for the cascaded constraint while the named
        # entity itself disappears.
        after = self._sketch(constraints=[dependent])
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [before]},
                {
                    "features": [after],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client, "d", "w", "e", "sketch1", remove_ids=["point1"]
        )

        assert result.apply.ok is False
        assert result.apply.mutation_verification == "failed"
        assert result.apply.reason_code == "REQUESTED_SKETCH_STATE_MISMATCH"

    @pytest.mark.asyncio
    async def test_retargeted_entity_requires_new_serialized_geometry(
        self, onshape_client
    ):
        before_point = {
            "btType": "BTMSketchPoint-158",
            "entityId": "point1",
            "x": 0.0,
            "y": 0.0,
            "isConstruction": False,
        }
        after_point = {**before_point, "x": 1.0, "y": 2.0}
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [self._sketch(entities=[before_point])]},
                {
                    "features": [self._sketch(entities=[after_point])],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client,
            "d",
            "w",
            "e",
            "sketch1",
            remove_ids=["point1"],
            add_entities=[{"type": "point", "id": "point1", "at": [1000, 2000]}],
        )

        assert result.apply.ok is True
        assert result.apply.mutation_verification == "verified"

    @pytest.mark.asyncio
    async def test_retargeted_constraint_requires_new_entity_reference(
        self, onshape_client
    ):
        before = self._horizontal_constraint("c1", "lineA")
        after = self._horizontal_constraint("c1", "lineB")
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [self._sketch(constraints=[before])]},
                {
                    "features": [self._sketch(constraints=[after])],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client,
            "d",
            "w",
            "e",
            "sketch1",
            remove_ids=["c1"],
            add_constraints=[{"type": "HORIZONTAL", "entity": "lineB", "id": "c1"}],
        )

        assert result.apply.ok is True
        assert result.apply.mutation_verification == "verified"

    @pytest.mark.asyncio
    async def test_safely_comparable_constraint_retarget_mismatch_is_failed(
        self, onshape_client
    ):
        old = self._horizontal_constraint("c1", "lineA")
        onshape_client.get = AsyncMock(
            side_effect=[
                {"features": [self._sketch(constraints=[old])]},
                {
                    "features": [self._sketch(constraints=[old])],
                    "featureStates": {"sketch1": {"featureStatus": "OK"}},
                },
            ]
        )
        onshape_client.post = AsyncMock(
            return_value={
                "feature": {"featureId": "sketch1", "btType": "BTMSketch-151"},
                "featureState": {"featureStatus": "OK"},
            }
        )

        result = await edit_sketch(
            onshape_client,
            "d",
            "w",
            "e",
            "sketch1",
            remove_ids=["c1"],
            add_constraints=[{"type": "HORIZONTAL", "entity": "lineB", "id": "c1"}],
        )

        assert result.apply.ok is False
        assert result.apply.mutation_verification == "failed"
        assert result.apply.reason_code == "REQUESTED_SKETCH_STATE_MISMATCH"
