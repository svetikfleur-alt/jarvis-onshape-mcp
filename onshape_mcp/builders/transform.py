"""Deterministic builder for the supported native Transform subset.

WP006 supports only world-coordinate translation of selected bodies with
``makeCopy=false``. Rotation, scaling, copying, mate-connector transforms,
and entity-directed translations are intentionally not exposed.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Union

from ._units import Length, parse_length


class TransformBuilder:
    """Build a native translation-only Transform feature payload."""

    def __init__(
        self,
        name: str = "Move body",
        translation_x: Union[float, int, str] = 0.0,
        translation_y: Union[float, int, str] = 0.0,
        translation_z: Union[float, int, str] = 0.0,
    ) -> None:
        self.name = name
        self.translation_x = translation_x
        self.translation_y = translation_y
        self.translation_z = translation_z
        self.body_queries: List[str] = []

    def add_body(self, body_id: str) -> "TransformBuilder":
        """Add one deterministic body ID to the transform selection."""

        if not isinstance(body_id, str) or not body_id.strip():
            raise ValueError("body_id must be a non-empty string")
        self.body_queries.append(body_id)
        return self

    @staticmethod
    def _quantity(parameter_id: str, parsed: Length) -> Dict[str, Any]:
        return {
            "btType": "BTMParameterQuantity-147",
            "expression": parsed.expression,
            "parameterId": parameter_id,
        }

    def build(self) -> Dict[str, Any]:
        """Return the exact native world-translation request envelope."""

        if not self.body_queries:
            raise ValueError("At least one body must be added")

        dx = parse_length(self.translation_x)
        dy = parse_length(self.translation_y)
        dz = parse_length(self.translation_z)
        meters = (dx.meters, dy.meters, dz.meters)
        if not all(math.isfinite(value) for value in meters):
            raise ValueError("translation values must be finite lengths")
        if all(value == 0.0 for value in meters):
            raise ValueError("translation must move at least one axis")

        return {
            "btType": "BTFeatureDefinitionCall-1406",
            "feature": {
                "btType": "BTMFeature-134",
                "featureType": "transform",
                "name": self.name,
                "parameters": [
                    {
                        "btType": "BTMParameterQueryList-148",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": list(self.body_queries),
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
                    self._quantity("dx", dx),
                    self._quantity("dy", dy),
                    self._quantity("dz", dz),
                    {
                        "btType": "BTMParameterBoolean-144",
                        "value": False,
                        "parameterId": "makeCopy",
                    },
                ],
            },
        }
