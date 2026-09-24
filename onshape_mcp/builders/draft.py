"""Deterministic builder for the supported native Draft subset.

WP006 supports only Onshape's neutral-plane Draft feature. Parting-line,
tangent-propagation, and re-fillet variants are intentionally not exposed.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Union

from ._units import parse_angle


class DraftBuilder:
    """Build a native neutral-plane Draft feature payload."""

    def __init__(
        self,
        name: str = "Draft",
        neutral_plane_id: str | None = None,
        angle: Union[float, int, str] = 3.0,
        reverse_pull_direction: bool = False,
    ) -> None:
        self.name = name
        self.neutral_plane_id = neutral_plane_id
        self.angle = angle
        self.reverse_pull_direction = reverse_pull_direction
        self.face_queries: List[str] = []

    def add_face(self, face_id: str) -> "DraftBuilder":
        """Add one deterministic face ID to the draft selection."""

        if not isinstance(face_id, str) or not face_id.strip():
            raise ValueError("face_id must be a non-empty string")
        self.face_queries.append(face_id)
        return self

    def build(self) -> Dict[str, Any]:
        """Return the exact native neutral-plane Draft request envelope."""

        if not isinstance(self.neutral_plane_id, str) or not self.neutral_plane_id.strip():
            raise ValueError("neutral_plane_id is required")
        if not self.face_queries:
            raise ValueError("At least one draft face must be added")

        parsed_angle = parse_angle(self.angle)
        maximum = math.radians(89.9)
        if (
            not math.isfinite(parsed_angle.radians)
            or parsed_angle.radians <= 0
            or parsed_angle.radians >= maximum
        ):
            raise ValueError(
                "Draft angle must be greater than 0 and less than 89.9 degrees"
            )

        return {
            "btType": "BTFeatureDefinitionCall-1406",
            "feature": {
                "btType": "BTMFeature-134",
                "featureType": "draft",
                "name": self.name,
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
                                "deterministicIds": [self.neutral_plane_id],
                            }
                        ],
                        "parameterId": "neutralPlane",
                    },
                    {
                        "btType": "BTMParameterQueryList-148",
                        "queries": [
                            {
                                "btType": "BTMIndividualQuery-138",
                                "deterministicIds": list(self.face_queries),
                            }
                        ],
                        "parameterId": "draftFaces",
                    },
                    {
                        "btType": "BTMParameterQuantity-147",
                        "expression": parsed_angle.expression,
                        "parameterId": "angle",
                    },
                    {
                        "btType": "BTMParameterBoolean-144",
                        "value": self.reverse_pull_direction,
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
