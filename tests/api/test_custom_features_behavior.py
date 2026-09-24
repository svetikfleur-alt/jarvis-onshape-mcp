"""Offline behavior tests for custom FeatureScript orchestration."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from onshape_mcp.api.custom_features import (
    CustomFeatureManager,
    _build_namespace,
    _to_onshape_parameter,
)
from onshape_mcp.api.feature_apply import FeatureApplyResult


class _RecordingClient:
    def __init__(
        self,
        *,
        get_results: list[Any] | None = None,
        post_results: list[Any] | None = None,
    ):
        self.get_results = deque(get_results or [])
        self.post_results = deque(post_results or [])
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def get(self, path: str) -> Any:
        self.calls.append(("GET", path, None))
        return self.get_results.popleft()

    async def post(self, path: str, *, data: dict[str, Any]) -> Any:
        self.calls.append(("POST", path, data))
        return self.post_results.popleft()


def _apply_result(*, ok: bool = True) -> FeatureApplyResult:
    return FeatureApplyResult(
        ok=ok,
        status="OK" if ok else "ERROR",
        feature_id="synthetic-applied-feature",
        feature_name="Synthetic feature",
        feature_type="syntheticFeature",
        error_message=None if ok else "REGEN_ERROR (ERROR)",
        regen_ok=ok,
        mutation_verification="verified" if ok else "failed",
    )


def _manager_after_version_discovery(client: _RecordingClient) -> CustomFeatureManager:
    """Build a manager for tests aimed below the version-preflight boundary."""

    manager = CustomFeatureManager(client)
    manager._std_version_cache = "2931"
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        ([{"name": "Start"}, {"name": "2930.0"}, {"name": "2931.0"}], "2931"),
        ([{"name": " 2940.12 "}, {"name": "Start"}], "2940"),
    ],
)
async def test_discover_fs_version_returns_latest_parseable_entry(versions, expected):
    client = _RecordingClient(get_results=[versions])

    result = await CustomFeatureManager(client).discover_fs_version()

    assert result == expected
    assert client.calls == [
        (
            "GET",
            "/api/v9/documents/d/12312312345abcabcabcdeff/versions",
            None,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, [], "wrong-shape"])
async def test_discover_fs_version_rejects_empty_or_wrong_response_shapes(response):
    manager = CustomFeatureManager(_RecordingClient(get_results=[response]))

    with pytest.raises(RuntimeError, match="unexpected shape"):
        await manager.discover_fs_version()


@pytest.mark.asyncio
async def test_discover_fs_version_rejects_lists_without_parseable_versions():
    manager = CustomFeatureManager(
        _RecordingClient(
            get_results=[[{"name": "Start"}, {"name": "release-candidate"}, {}]]
        )
    )

    with pytest.raises(RuntimeError, match="entry_count=3"):
        await manager.discover_fs_version()


@pytest.mark.asyncio
async def test_feature_studio_lifecycle_builds_expected_routes_and_payloads():
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {"microversionId": "synthetic-micro"}],
        get_results=[{"featureSpecs": []}],
    )
    manager = CustomFeatureManager(client)

    fs_id = await manager.create_feature_studio("doc", "workspace", "Generated tools")
    upload = await manager.upload_fs_source(
        "doc", "workspace", fs_id, "FeatureScript 2931;"
    )
    specs = await manager.get_featurespecs("doc", "workspace", fs_id)

    assert fs_id == "synthetic-fs"
    assert upload == {"microversionId": "synthetic-micro"}
    assert specs == {"featureSpecs": []}
    assert client.calls == [
        (
            "POST",
            "/api/v9/featurestudios/d/doc/w/workspace",
            {"name": "Generated tools"},
        ),
        (
            "POST",
            "/api/v9/featurestudios/d/doc/w/workspace/e/synthetic-fs",
            {"contents": "FeatureScript 2931;"},
        ),
        (
            "GET",
            "/api/v9/featurestudios/d/doc/w/workspace/e/synthetic-fs/featurespecs",
            None,
        ),
    ]


@pytest.mark.asyncio
async def test_create_feature_studio_rejects_response_without_element_id():
    manager = CustomFeatureManager(_RecordingClient(post_results=[{"name": "missing"}]))

    with pytest.raises(RuntimeError, match="creation returned no id"):
        await manager.create_feature_studio("doc", "workspace", "Generated tools")


@pytest.mark.asyncio
async def test_instantiate_custom_feature_converts_parameters_and_builds_namespace(monkeypatch):
    captured: dict[str, Any] = {}
    expected = _apply_result()

    async def fake_apply(
        client: Any,
        document_id: str,
        workspace_id: str,
        element_id: str,
        payload: dict[str, Any],
        *,
        operation: str,
    ) -> FeatureApplyResult:
        captured.update(
            client=client,
            ids=(document_id, workspace_id, element_id),
            payload=payload,
            operation=operation,
        )
        return expected

    monkeypatch.setattr("onshape_mcp.api.custom_features.apply_feature_and_check", fake_apply)
    client = _RecordingClient()
    manager = CustomFeatureManager(client)

    result = await manager.instantiate_custom_feature(
        "doc",
        "workspace",
        "part-studio",
        fs_element_id="feature-studio",
        source_microversion_id="microversion",
        feature_type="syntheticFeature_2",
        feature_name="Synthetic feature",
        parameters=[
            {"id": "length", "type": "quantity", "value": "5 mm"},
            {"id": "count", "type": "real", "value": 2.5},
            {"id": "label", "type": "string", "value": None},
            {"id": "enabled", "type": "boolean", "value": True},
        ],
    )

    assert result is expected
    assert captured["client"] is client
    assert captured["ids"] == ("doc", "workspace", "part-studio")
    assert captured["operation"] == "create"
    assert captured["payload"] == {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "syntheticFeature_2",
            "name": "Synthetic feature",
            "namespace": "efeature-studio::mmicroversion",
            "suppressed": False,
            "parameters": [
                {
                    "btType": "BTMParameterQuantity-147",
                    "parameterId": "length",
                    "expression": "5 mm",
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "parameterId": "count",
                    "isInteger": False,
                    "value": 2.5,
                    "expression": "2.5",
                },
                {
                    "btType": "BTMParameterString-149",
                    "parameterId": "label",
                    "value": "",
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "parameterId": "enabled",
                    "value": True,
                },
            ],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("feature_type", ["", "contains space", "2startsWithNumber", "bad-name"])
async def test_instantiate_rejects_invalid_featurescript_identifiers(feature_type):
    manager = CustomFeatureManager(_RecordingClient())

    with pytest.raises(ValueError, match="valid FS identifier"):
        await manager.instantiate_custom_feature(
            "doc",
            "workspace",
            "part-studio",
            fs_element_id="feature-studio",
            source_microversion_id="microversion",
            feature_type=feature_type,
            feature_name="Synthetic feature",
        )


@pytest.mark.asyncio
async def test_instantiate_requires_source_microversion_before_applying():
    manager = CustomFeatureManager(_RecordingClient())

    with pytest.raises(ValueError, match="source_microversion_id is required"):
        await manager.instantiate_custom_feature(
            "doc",
            "workspace",
            "part-studio",
            fs_element_id="feature-studio",
            source_microversion_id="",
            feature_type="syntheticFeature",
            feature_name="Synthetic feature",
        )


@pytest.mark.parametrize(
    ("descriptor", "expected"),
    [
        (
            {"id": "length", "type": "QUANTITY", "value": 5},
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "length",
                "expression": "5",
            },
        ),
        (
            {"id": "label", "type": "string", "value": 7},
            {"btType": "BTMParameterString-149", "parameterId": "label", "value": "7"},
        ),
        (
            {"id": "enabled", "type": "boolean", "value": False},
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "enabled",
                "value": False,
            },
        ),
        (
            {"id": "ratio", "type": "real", "value": None},
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "ratio",
                "isInteger": False,
                "value": 0.0,
                "expression": "0",
            },
        ),
    ],
)
def test_parameter_conversion_normalizes_supported_values(descriptor, expected):
    assert _to_onshape_parameter(descriptor) == expected


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        ({"type": "string", "value": "x"}, "parameter missing id"),
        ({"id": "shape", "type": "query", "value": "x"}, "unsupported parameter type"),
    ],
)
def test_parameter_conversion_rejects_missing_ids_and_unknown_types(descriptor, message):
    with pytest.raises(ValueError, match=message):
        _to_onshape_parameter(descriptor)


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true"])
def test_boolean_parameter_rejects_non_boolean_values(value):
    with pytest.raises(ValueError, match="boolean parameter .* requires true or false"):
        _to_onshape_parameter({"id": "enabled", "type": "boolean", "value": value})


def test_namespace_uses_supported_element_and_microversion_prefixes():
    assert _build_namespace("feature-studio", "microversion") == (
        "efeature-studio::mmicroversion"
    )


@pytest.mark.asyncio
async def test_apply_featurescript_feature_uses_matching_spec_and_default_element_name(
    monkeypatch,
):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {"microversionId": "upload-fallback"}],
        get_results=[
            {
                "featureSpecs": [
                    {"featureType": "other", "sourceMicroversionId": "other-micro"},
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    },
                ],
                "libraryVersion": 2931,
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    expected = _apply_result()
    captured: dict[str, Any] = {}

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;",
        feature_name="Synthetic feature",
        parameters=[{"id": "enabled", "type": "boolean", "value": True}],
    )

    assert result == {
        "apply_result": expected,
        "fs_element_id": "synthetic-fs",
        "source_microversion_id": "selected-micro",
        "fs_library_version": 2931,
    }
    assert client.calls[0] == (
        "POST",
        "/api/v9/featurestudios/d/doc/w/workspace",
        {"name": "ClaudeFS_syntheticFeature"},
    )
    assert captured["args"] == ("doc", "workspace", "part-studio")
    assert captured["kwargs"] == {
        "fs_element_id": "synthetic-fs",
        "source_microversion_id": "selected-micro",
        "feature_type": "syntheticFeature",
        "feature_name": "Synthetic feature",
        "parameters": [{"id": "enabled", "type": "boolean", "value": True}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upload", "expected_microversion"),
    [
        ({"sourceMicroversion": "source-fallback"}, "source-fallback"),
        ({"microversionId": "id-fallback"}, "id-fallback"),
    ],
)
async def test_apply_featurescript_feature_falls_back_to_upload_microversion(
    monkeypatch, upload, expected_microversion
):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, upload],
        get_results=[
            {
                "featureSpecs": [{"featureType": "first"}],
                "libraryVersion": "2931",
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    captured: dict[str, Any] = {}

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args
        captured.update(kwargs)
        return _apply_result()

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;",
        feature_name="Synthetic feature",
        fs_element_name="Explicit studio name",
    )

    assert result["source_microversion_id"] == expected_microversion
    assert captured["source_microversion_id"] == expected_microversion
    assert client.calls[0][2] == {"name": "Explicit studio name"}


@pytest.mark.asyncio
async def test_apply_featurescript_feature_rejects_empty_compiled_specs():
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[{"featureSpecs": []}],
    )
    manager = _manager_after_version_discovery(client)

    with pytest.raises(RuntimeError, match="compiled to an empty feature spec"):
        await manager.apply_featurescript_feature(
            "doc",
            "workspace",
            "part-studio",
            feature_type="syntheticFeature",
            feature_script="FeatureScript 2931;",
            feature_name="Synthetic feature",
        )


@pytest.mark.asyncio
async def test_failed_apply_is_enriched_with_featurescript_notices(monkeypatch):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    failed = _apply_result(ok=False)
    captured: dict[str, Any] = {}

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        return failed

    async def fake_fetch(*args: Any) -> list[dict[str, str]]:
        captured["fetch_args"] = args
        return [{"level": "ERROR", "message": "synthetic operation failed"}]

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: "opBad();")
    monkeypatch.setattr("onshape_mcp.api.custom_features.fetch_body_notices", fake_fetch)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert result["apply_result"].error_message == (
        "REGEN_ERROR (ERROR)\nFS NOTICES:\n[ERROR] synthetic operation failed"
    )
    assert captured["fetch_args"] == (
        client,
        "doc",
        "workspace",
        "part-studio",
        "opBad();",
    )


@pytest.mark.asyncio
async def test_failed_apply_cleans_up_only_the_exact_created_feature(monkeypatch):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    captured: dict[str, Any] = {}

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        return _apply_result(ok=False)

    async def fake_delete(
        partstudio_manager: Any,
        document_id: str,
        workspace_id: str,
        element_id: str,
        feature_id: str,
    ) -> FeatureApplyResult:
        captured.update(
            manager=partstudio_manager,
            ids=(document_id, workspace_id, element_id, feature_id),
        )
        return FeatureApplyResult(
            ok=True,
            status="OK",
            feature_id=feature_id,
            feature_name="Synthetic feature",
            feature_type="syntheticFeature",
            mutation_verification="verified",
            changed=True,
            reason_code="FEATURE_ABSENCE_VERIFIED",
        )

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr(
        "onshape_mcp.api.custom_features.delete_partstudio_feature_and_check",
        fake_delete,
        raising=False,
    )
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: None)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert captured["ids"] == (
        "doc",
        "workspace",
        "part-studio",
        "synthetic-applied-feature",
    )
    assert result["cleanup"] == {
        "attempted": True,
        "ok": True,
        "status": "OK",
        "feature_id": "synthetic-applied-feature",
        "feature_type": "syntheticFeature",
        "feature_name": "Synthetic feature",
        "error_message": None,
        "transport_ok": None,
        "http_ok": None,
        "regen_ok": None,
        "mutation_verification": "verified",
        "changed": True,
        "verification_scope": "none",
        "reason_code": "FEATURE_ABSENCE_VERIFIED",
        "verification_message": None,
    }


@pytest.mark.asyncio
async def test_failed_apply_without_exact_feature_id_reports_cleanup_not_attempted(
    monkeypatch,
):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        failed = _apply_result(ok=False)
        failed.feature_id = ""
        return failed

    async def unexpected_delete(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        raise AssertionError("cleanup must not guess a feature id")

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr(
        "onshape_mcp.api.custom_features.delete_partstudio_feature_and_check",
        unexpected_delete,
        raising=False,
    )
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: None)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert result["cleanup"] == {
        "attempted": False,
        "ok": False,
        "feature_id": "",
        "reason_code": "CUSTOM_FEATURE_CLEANUP_ID_UNAVAILABLE",
        "error_message": "Cleanup was not attempted because the failed feature has no exact feature ID.",
    }


@pytest.mark.asyncio
async def test_cleanup_exception_is_reported_without_hiding_primary_failure(monkeypatch):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        return _apply_result(ok=False)

    async def broken_delete(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr(
        "onshape_mcp.api.custom_features.delete_partstudio_feature_and_check",
        broken_delete,
        raising=False,
    )
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: None)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert result["apply_result"].error_message == "REGEN_ERROR (ERROR)"
    assert result["cleanup"]["attempted"] is True
    assert result["cleanup"]["ok"] is False
    assert result["cleanup"]["feature_id"] == "synthetic-applied-feature"
    assert result["cleanup"]["reason_code"] == "CUSTOM_FEATURE_CLEANUP_FAILED"
    assert "synthetic cleanup failure" in result["cleanup"]["error_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verification", "regen_ok"),
    [("unverified", None), ("no_effect", True), ("failed", True)],
)
async def test_nonconclusive_apply_does_not_trigger_cleanup(
    monkeypatch, verification, regen_ok
):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        result = _apply_result(ok=False)
        result.mutation_verification = verification
        result.regen_ok = regen_ok
        return result

    async def unexpected_delete(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        raise AssertionError("cleanup requires a conclusive failed state")

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr(
        "onshape_mcp.api.custom_features.delete_partstudio_feature_and_check",
        unexpected_delete,
        raising=False,
    )
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: None)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert "cleanup" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, ""])
async def test_failed_apply_without_extractable_body_preserves_original_error(
    monkeypatch, body
):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    failed = _apply_result(ok=False)

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        return failed

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: body)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nnot a defineFeature",
        feature_name="Synthetic feature",
    )

    assert result["apply_result"].error_message == "REGEN_ERROR (ERROR)"


@pytest.mark.asyncio
async def test_failed_apply_with_no_renderable_notices_omits_empty_notice_header(
    monkeypatch,
):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    failed = _apply_result(ok=False)

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        return failed

    async def empty_fetch(*args: Any) -> list[dict[str, str]]:
        del args
        return []

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr(
        "onshape_mcp.api.custom_features.extract_fs_body", lambda source: "opBad();"
    )
    monkeypatch.setattr("onshape_mcp.api.custom_features.fetch_body_notices", empty_fetch)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert result["apply_result"].error_message == "REGEN_ERROR (ERROR)"


@pytest.mark.asyncio
async def test_notice_enrichment_failure_preserves_original_apply_diagnostic(monkeypatch):
    client = _RecordingClient(
        post_results=[{"id": "synthetic-fs"}, {}],
        get_results=[
            {
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "sourceMicroversionId": "selected-micro",
                    }
                ]
            }
        ],
    )
    manager = _manager_after_version_discovery(client)
    failed = _apply_result(ok=False)

    async def fake_instantiate(*args: Any, **kwargs: Any) -> FeatureApplyResult:
        del args, kwargs
        return failed

    async def broken_fetch(*args: Any) -> list[dict[str, str]]:
        del args
        raise RuntimeError("synthetic enrichment failure")

    monkeypatch.setattr(manager, "instantiate_custom_feature", fake_instantiate)
    monkeypatch.setattr("onshape_mcp.api.custom_features.extract_fs_body", lambda source: "opBad();")
    monkeypatch.setattr("onshape_mcp.api.custom_features.fetch_body_notices", broken_fetch)

    result = await manager.apply_featurescript_feature(
        "doc",
        "workspace",
        "part-studio",
        feature_type="syntheticFeature",
        feature_script="FeatureScript 2931;\nsynthetic source",
        feature_name="Synthetic feature",
    )

    assert result["apply_result"].error_message == "REGEN_ERROR (ERROR)"
