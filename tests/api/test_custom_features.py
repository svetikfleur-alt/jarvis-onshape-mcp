"""Focused regression tests for custom FeatureScript orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from onshape_mcp.api.custom_features import (
    CustomFeatureManager,
    FeatureScriptVersionDiscoveryError,
)


@pytest.mark.asyncio
async def test_missing_source_microversion_reports_selected_spec_keys():
    """A malformed compiled spec raises a useful error, never ``NameError``."""
    client = SimpleNamespace(
        post=AsyncMock(side_effect=[{"id": "synthetic-fs"}, {}]),
        get=AsyncMock(
            return_value={
                "featureSpecs": [
                    {
                        "featureType": "syntheticFeature",
                        "displayName": "Synthetic feature",
                    }
                ],
                "libraryVersion": 2931,
            }
        ),
    )
    manager = CustomFeatureManager(client)
    manager._std_version_cache = "2931"

    with pytest.raises(RuntimeError) as exc_info:
        await manager.apply_featurescript_feature(
            "synthetic-document",
            "synthetic-workspace",
            "synthetic-part-studio",
            feature_type="syntheticFeature",
            feature_script="FeatureScript 2931;",
            feature_name="Synthetic feature",
        )

    message = str(exc_info.value)
    assert "Could not extract sourceMicroversionId" in message
    assert "featureType" in message
    assert "displayName" in message


@pytest.mark.asyncio
async def test_version_discovery_error_suppresses_raw_http_exception_context():
    request = httpx.Request(
        "GET",
        "https://cad.onshape.com/api/v9/documents/d/private-document-canary"
        "?token=private-query-canary",
    )
    client = SimpleNamespace(
        get=AsyncMock(side_effect=httpx.ConnectError("private-body-canary", request=request))
    )
    manager = CustomFeatureManager(client)

    with pytest.raises(FeatureScriptVersionDiscoveryError) as caught:
        await manager.validate_featurescript_source("FeatureScript 2931;")

    rendered = str(caught.value)
    assert "private-document-canary" not in rendered
    assert "private-query-canary" not in rendered
    assert "private-body-canary" not in rendered
    assert caught.value.__cause__ is None
