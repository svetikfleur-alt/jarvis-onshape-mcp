"""Bounded, read-only engineering projections over authoritative CAD reads."""

from __future__ import annotations

from collections import Counter
from typing import Any

import jsonschema

from .api.sketch_inspect import inspect_sketch
from .governance.features import FeatureIndex


MAX_REFERENCE_EVIDENCE = 12
MAX_FEATURE_PARAMETERS = 40
MAX_FEATURE_DEPENDENCIES = 20
_MAX_TEXT = 256
_DIMENSIONAL_CONSTRAINTS = frozenset(
    {"ANGLE", "DIAMETER", "DISTANCE", "LENGTH", "RADIUS"}
)
_TARGET_PROPERTIES = {
    "documentId": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r".*\S.*"},
    "workspaceId": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r".*\S.*"},
    "elementId": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r".*\S.*"},
}
_TARGET_REQUIRED = ["documentId", "workspaceId", "elementId"]

PERCEPTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "inspect_sketch_health": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_TARGET_PROPERTIES,
            "sketchFeatureId": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r".*\S.*",
            },
            "sketchName": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "pattern": r".*\S.*",
            },
        },
        "required": _TARGET_REQUIRED,
    },
    "inspect_feature_context": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_TARGET_PROPERTIES,
            "featureId": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r".*\S.*",
            },
            "parameterLimit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_FEATURE_PARAMETERS,
                "default": 20,
            },
            "dependencyLimit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_FEATURE_DEPENDENCIES,
                "default": MAX_FEATURE_DEPENDENCIES,
            },
        },
        "required": [*_TARGET_REQUIRED, "featureId"],
    },
    "get_visual_snapshot": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_TARGET_PROPERTIES,
            "views": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["iso", "front", "top", "right", "back", "bottom", "left"],
                },
                "minItems": 1,
                "maxItems": 4,
                "uniqueItems": True,
                "default": ["iso", "front", "top", "right"],
            },
            "width": {
                "type": "integer",
                "minimum": 128,
                "maximum": 1024,
                "default": 640,
            },
            "height": {
                "type": "integer",
                "minimum": 128,
                "maximum": 1024,
                "default": 480,
            },
            "edges": {"type": "boolean", "default": True},
        },
        "required": _TARGET_REQUIRED,
    },
}


def _bounded_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def validate_perception_arguments(tool_name: str, arguments: Any) -> dict[str, Any]:
    """Validate a perception request before any provider call."""

    schema = PERCEPTION_SCHEMAS[tool_name]
    try:
        jsonschema.validate(instance=arguments, schema=schema)
    except jsonschema.ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "request"
        raise ValueError(
            f"Invalid {tool_name} request at {location}: "
            f"{error.validator} constraint failed"
        ) from error
    return arguments


def inspect_sketch_health(
    features_doc: dict[str, Any],
    *,
    sketch_feature_id: str | None = None,
    sketch_name: str | None = None,
    reference_sample_limit: int = MAX_REFERENCE_EVIDENCE,
) -> dict[str, Any]:
    """Summarize sketch engineering evidence without claiming solver state."""

    summary = inspect_sketch(
        features_doc,
        sketch_feature_id=sketch_feature_id,
        sketch_name=sketch_name,
    )
    entities = summary["entities"]
    constraints = summary["constraints"]
    entity_counts = Counter(_bounded_text(row.get("kind") or "other", 64) for row in entities)
    constraint_counts = Counter(
        _bounded_text(row.get("constraintType") or "UNKNOWN", 64) for row in constraints
    )
    dimensional_count = sum(
        count
        for constraint_type, count in constraint_counts.items()
        if constraint_type.upper() in _DIMENSIONAL_CONSTRAINTS
    )

    external_rows: list[dict[str, Any]] = []
    external_reference_count = 0
    for constraint in constraints:
        references = {
            key: _bounded_text(constraint[key], 128)
            for key in ("externalFirst", "externalSecond")
            if constraint.get(key)
        }
        if not references:
            continue
        external_reference_count += len(references)
        external_rows.append(
            {
                "constraint_id": _bounded_text(constraint.get("id"), 128),
                "constraint_type": _bounded_text(
                    constraint.get("constraintType") or "UNKNOWN", 64
                ),
                "references": references,
            }
        )

    states = features_doc.get("featureStates")
    state = states.get(summary["feature_id"], {}) if isinstance(states, dict) else {}
    messages = state.get("messages") if isinstance(state, dict) else []
    message_count = len(messages) if isinstance(messages, list) else 0
    warnings: list[str] = []
    if str(summary["status"]).upper() not in {"OK", "?"}:
        warnings.append(f"Sketch regeneration status is {_bounded_text(summary['status'], 64)}")
    if message_count:
        warnings.append(
            f"Authoritative feature state contains {message_count} regeneration message(s)"
        )

    evidence_limit = max(1, min(int(reference_sample_limit), MAX_REFERENCE_EVIDENCE))
    evidence_rows = external_rows[:evidence_limit]
    return {
        "contract": "jarvis.sketch_health.v1",
        "sketch": {
            "feature_id": _bounded_text(summary["feature_id"], 128),
            "name": _bounded_text(summary["name"]),
            "status": _bounded_text(summary["status"], 64),
        },
        "geometry": {
            "entity_count": len(entities),
            "counts_by_type": dict(sorted(entity_counts.items())),
        },
        "constraints": {
            "constraint_count": len(constraints),
            "dimensional_count": dimensional_count,
            "geometric_count": len(constraints) - dimensional_count,
            "counts_by_type": dict(sorted(constraint_counts.items())),
        },
        "references": {
            "external_reference_count": external_reference_count,
            "evidence": {
                "count": len(external_rows),
                "returned_count": len(evidence_rows),
                "rows": evidence_rows,
                "truncated": len(evidence_rows) < len(external_rows),
            },
        },
        "constraint_health": {
            "state": "UNKNOWN",
            "remaining_dof": "unavailable",
            "source": "unavailable",
        },
        "warnings": warnings,
        "unknown_or_unavailable": [
            "fully_constrained_state",
            "remaining_degrees_of_freedom",
        ],
    }


def inspect_feature_context(
    features_doc: dict[str, Any],
    feature_id: str,
    *,
    parameter_limit: int = 20,
    dependency_limit: int = MAX_FEATURE_DEPENDENCIES,
) -> dict[str, Any]:
    """Build one bounded feature projection from one complete feature document."""

    safe_parameter_limit = max(1, min(int(parameter_limit), MAX_FEATURE_PARAMETERS))
    safe_dependency_limit = max(1, min(int(dependency_limit), MAX_FEATURE_DEPENDENCIES))
    index = FeatureIndex.build(features_doc)
    detail = index.inspect(feature_id, max_parameters=safe_parameter_limit)
    dependencies = index.dependencies(feature_id, limit=safe_dependency_limit)
    parameters = {
        _bounded_text(parameter.get("parameterId"), 128): parameter.get("value_summary")
        for parameter in detail["parameters"]
        if parameter.get("parameterId")
    }
    upstream_rows = dependencies["upstream"]
    downstream_rows = dependencies["downstream"]
    unresolved_rows = dependencies["unresolved_references"]
    unresolved_count = dependencies["unresolved_reference_count"]
    reference_warnings: list[str] = []
    if unresolved_count:
        reference_warnings.append(
            f"{unresolved_count} reference(s) remain unresolved; no dependency edges were invented"
        )

    return {
        "contract": "jarvis.feature_context.v1",
        "feature": {
            "id": _bounded_text(detail["featureId"], 128),
            "name": _bounded_text(detail["name"]),
            "type": _bounded_text(detail["featureType"] or detail["btType"], 128),
            "status": _bounded_text(detail["status"], 64),
            "suppressed": bool(detail["suppressed"]),
        },
        "canonical_parameters": {
            "count": detail["parameter_count"],
            "returned_count": len(parameters),
            "items": parameters,
            "truncated": bool(detail["truncated"]),
        },
        "upstream": {
            "feature_ids": [row["featureId"] for row in upstream_rows],
            "count": dependencies["upstream_count"],
            "returned_count": len(upstream_rows),
            "truncated": len(upstream_rows) < dependencies["upstream_count"],
        },
        "downstream": {
            "immediate_feature_ids": [row["featureId"] for row in downstream_rows],
            "count": dependencies["downstream_count"],
            "returned_count": len(downstream_rows),
            "truncated": len(downstream_rows) < dependencies["downstream_count"],
        },
        "affected_bodies": {
            "count": "unavailable",
            "ids": [],
            "names": [],
            "source": "unavailable",
        },
        "reference_health": {
            "confirmed": {
                "count": dependencies["upstream_count"],
                "evidence_types": sorted(
                    {row["evidenceType"] for row in upstream_rows}
                ),
            },
            "unresolved": {
                "count": unresolved_count,
                "returned_count": len(unresolved_rows),
                "rows": unresolved_rows,
                "truncated": len(unresolved_rows) < unresolved_count,
            },
            "warnings": reference_warnings,
        },
        "source_revision": features_doc.get("sourceMicroversion") or "unavailable",
        "unknown_or_unavailable": ["affected_bodies"],
    }
