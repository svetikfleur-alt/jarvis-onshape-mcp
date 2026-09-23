"""Focused regression tests for custom FeatureScript orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from onshape_mcp.api.custom_features import CustomFeatureManager


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
