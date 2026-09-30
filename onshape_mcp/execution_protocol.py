"""Bounded agent-facing projections for deterministic CAD operations.

This module is an MCP adapter.  It deliberately owns no Onshape transport,
mutation verification, cache policy, or engineering decisions.  Callers pass
the already-sanitized public mutation result and authoritative read documents;
the helpers only normalize and bound what crosses the model boundary.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any, Mapping, Sequence

from .governance.features import FeatureIndex


_MAX_TEXT = 256
_MAX_REQUEST_ITEMS = 32
_MAX_BODIES = 32
_MAX_FAILURE_ROWS = 50
_MAX_COMPLEX_VALUE_ITEMS = 4
_MAX_VALUE_DEPTH = 2
_SUCCESSFUL_REGEN = frozenset({"OK", "INFO"})
_FEATURE_CACHE_STATES = frozenset(
    {"unchanged", "refreshed", "partially_refreshed", "invalidated"}
)
_TOPOLOGY_CACHE_STATES = frozenset({"unchanged", "invalidated"})
_TERMINAL_PREWRITE_NOOP_REASONS = frozenset(
    {"REQUESTED_STATE_ALREADY_PRESENT", "FEATURE_ALREADY_ABSENT"}
)

SUPPORTED_EXECUTION_OPERATIONS = frozenset(
    {
        "create_sketch",
        "create_extrude",
        "update_feature",
        "create_linear_pattern",
        "create_shell",
        "create_chamfer",
        "create_draft",
        "move_body",
    }
)

_CANONICAL_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "create_extrude": (
        ("name", "name"),
        ("sketchFeatureId", "sketch_feature_id"),
        ("depth", "depth"),
        ("variableDepth", "variable_depth"),
        ("operationType", "operation_type"),
        ("oppositeDirection", "opposite_direction"),
        ("forceOppositeDirection", "force_opposite_direction"),
        ("endType", "end_type"),
    ),
    "create_linear_pattern": (
        ("name", "name"),
        ("featureIds", "feature_ids"),
        ("distance", "distance"),
        ("count", "count"),
        ("directionEdgeId", "direction_edge_id"),
    ),
    "create_shell": (
        ("name", "name"),
        ("thickness", "thickness"),
        ("faceIds", "face_ids"),
        ("outward", "outward"),
        ("variableThickness", "variable_thickness"),
    ),
    "create_chamfer": (
        ("name", "name"),
        ("distance", "distance"),
        ("edgeIds", "edge_ids"),
        ("chamferType", "chamfer_type"),
        ("variableDistance", "variable_distance"),
    ),
    "create_draft": (
        ("name", "name"),
        ("neutralPlaneId", "neutral_plane_id"),
        ("faceIds", "face_ids"),
        ("angle", "angle"),
        ("reversePullDirection", "reverse_pull_direction"),
    ),
    "move_body": (
        ("name", "name"),
        ("bodyIds", "body_ids"),
        ("translationX", "translation_x"),
        ("translationY", "translation_y"),
        ("translationZ", "translation_z"),
    ),
}

_STATE_EFFECTS = {
    "create_sketch": ["feature_tree", "sketch_constraints"],
    "create_extrude": ["feature_tree", "bodies", "topology", "render"],
    "update_feature": ["feature_tree", "parameters", "bodies", "topology", "render"],
    "create_linear_pattern": ["feature_tree", "bodies", "topology", "render"],
    "create_shell": ["feature_tree", "bodies", "topology", "render"],
    "create_chamfer": ["feature_tree", "bodies", "topology", "render"],
    "create_draft": ["feature_tree", "bodies", "topology", "render"],
    "move_body": ["feature_tree", "bodies", "topology", "render"],
}


def _bounded_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value or "")
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}\u2026"


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, list):
        if depth >= _MAX_VALUE_DEPTH:
            return {
                "kind": "list",
                "count": len(value),
                "truncated": True,
            }
        if all(
            isinstance(item, (str, bool, int, float)) or item is None
            for item in value
        ):
            items = [
                _bounded_value(item, depth=depth + 1)
                for item in value[:_MAX_REQUEST_ITEMS]
            ]
            if len(items) == len(value):
                return items
            return {
                "kind": "list",
                "count": len(value),
                "items": items,
                "truncated": True,
            }
        items = [
            _bounded_value(item, depth=depth + 1)
            for item in value[:_MAX_COMPLEX_VALUE_ITEMS]
        ]
        return {
            "kind": "list",
            "count": len(value),
            "items": items,
            "truncated": len(items) < len(value)
            or any(
                isinstance(item, Mapping) and item.get("truncated") is True
                for item in items
            ),
        }
    return "unsupported value"


def _target(request: Mapping[str, Any]) -> dict[str, str]:
    return {
        "document_id": _bounded_text(request.get("documentId"), 128),
        "workspace_id": _bounded_text(request.get("workspaceId"), 128),
        "element_id": _bounded_text(request.get("elementId"), 128),
    }


def canonical_request(operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
    """Return the operation's bounded, reasoning-relevant requested parameters."""

    if operation not in SUPPORTED_EXECUTION_OPERATIONS:
        raise ValueError(f"Unsupported execution operation: {operation!r}")

    if operation == "create_sketch":
        entities = request.get("entities")
        constraints = request.get("constraints")
        result: dict[str, Any] = {}
        for source, destination in (
            ("name", "name"),
            ("plane", "plane"),
            ("faceId", "face_id"),
        ):
            if source in request:
                result[destination] = _bounded_value(request[source])
        result["entity_count"] = len(entities) if isinstance(entities, list) else 0
        result["constraint_count"] = (
            len(constraints) if isinstance(constraints, list) else 0
        )
        return result

    if operation == "update_feature":
        result = {"feature_id": _bounded_text(request.get("featureId"), 128)}
        updates_out: list[dict[str, Any]] = []
        updates = request.get("updates")
        if isinstance(updates, list):
            for update in updates[:_MAX_REQUEST_ITEMS]:
                if not isinstance(update, Mapping):
                    continue
                row: dict[str, Any] = {
                    "parameter_id": _bounded_text(update.get("parameterId"), 128)
                }
                for key in ("expression", "value"):
                    if key in update:
                        row[key] = _bounded_value(update[key])
                updates_out.append(row)
        result["updates"] = updates_out
        return result

    result = {}
    for source, destination in _CANONICAL_FIELDS[operation]:
        if source in request:
            result[destination] = _bounded_value(request[source])
    return result


def _compact_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if "summary" in changes:
        compact["summary"] = _bounded_text(changes.get("summary"), 512)
    for key in ("body_count_before", "body_count_after"):
        value = changes.get(key)
        if isinstance(value, int):
            compact[key] = value
    for key in ("faces_added", "faces_removed", "edges_added", "edges_removed"):
        explicit_count = changes.get(f"{key}_count")
        value = changes.get(key)
        if (
            isinstance(explicit_count, int)
            and not isinstance(explicit_count, bool)
            and explicit_count >= 0
        ):
            compact[f"{key}_count"] = explicit_count
        elif isinstance(value, list):
            compact[f"{key}_count"] = len(value)
    for key in ("volume_before_mm3", "volume_after_mm3", "volume_delta_mm3"):
        value = changes.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            compact[key] = value
    for key in ("bbox_before_mm", "bbox_after_mm"):
        value = changes.get(key)
        if isinstance(value, Mapping):
            compact[key] = {
                _bounded_text(axis, 32): coordinate
                for axis, coordinate in value.items()
                if isinstance(coordinate, (int, float)) and not isinstance(coordinate, bool)
            }
    return compact


def _invalidation_projection(
    operation: str, legacy_result: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Prefer bounded runtime invalidation evidence over static expectations."""

    raw = legacy_result.get("invalidation")
    if not isinstance(raw, Mapping):
        return list(_STATE_EFFECTS[operation]), {
            "basis": "conservative_expectation"
        }

    feature_cache = raw.get("feature_cache")
    topology_cache = raw.get("topology_cache")
    topology_evicted = raw.get("topology_snapshot_evicted")
    contexts_updated = raw.get("contexts_updated")
    has_runtime_evidence = (
        feature_cache in _FEATURE_CACHE_STATES
        and topology_cache in _TOPOLOGY_CACHE_STATES
        and isinstance(topology_evicted, bool)
    )
    if not has_runtime_evidence:
        return list(_STATE_EFFECTS[operation]), {
            "basis": "conservative_expectation"
        }

    projection: dict[str, Any] = {"basis": "actual"}
    if (
        isinstance(contexts_updated, int)
        and not isinstance(contexts_updated, bool)
        and contexts_updated >= 0
    ):
        projection["contexts_updated"] = contexts_updated
    if feature_cache in _FEATURE_CACHE_STATES:
        projection["feature_cache"] = feature_cache
    if topology_cache in _TOPOLOGY_CACHE_STATES:
        projection["topology_cache"] = topology_cache
    if isinstance(topology_evicted, bool):
        projection["topology_snapshot_evicted"] = topology_evicted

    categories: list[str] = []
    if feature_cache in {"partially_refreshed", "invalidated"}:
        categories.append("feature_tree")
    if topology_evicted is True:
        categories.append("bodies")
    if topology_cache == "invalidated" or topology_evicted is True:
        categories.append("topology")
    return categories, projection


def _is_terminal_prewrite_noop(
    mutation_state: str,
    reason_code: str | None,
    legacy_result: Mapping[str, Any],
) -> bool:
    if (
        mutation_state != "no_effect"
        or reason_code not in _TERMINAL_PREWRITE_NOOP_REASONS
        or legacy_result.get("changed") is not False
        or legacy_result.get("transport_ok") is not None
        or legacy_result.get("http_ok") is not None
    ):
        return False
    if reason_code == "REQUESTED_STATE_ALREADY_PRESENT":
        return legacy_result.get("regen_ok") is True
    return True


def compact_execution_result(
    operation: str,
    request: Mapping[str, Any],
    legacy_result: Mapping[str, Any],
    *,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Project one existing verified mutation response into the shared contract."""

    if operation not in SUPPORTED_EXECUTION_OPERATIONS:
        raise ValueError(f"Unsupported execution operation: {operation!r}")
    if response_mode not in {"compact", "diagnostic"}:
        raise ValueError("response_mode must be 'compact' or 'diagnostic'")

    verification = legacy_result.get("mutation_verification")
    mutation_state = (
        verification
        if verification in {"verified", "unverified", "no_effect", "failed"}
        else "unverified"
    )
    reason_code = _bounded_text(legacy_result.get("reason_code"), 128) or None
    error_message = (
        _bounded_text(
            legacy_result.get("error_message")
            or legacy_result.get("verification_message"),
            512,
        )
        or None
    )
    hints = legacy_result.get("hints")
    recovery_hint = None
    if isinstance(hints, list) and hints:
        recovery_hint = _bounded_text(hints[0], 512)
    invalidated_categories, invalidation = _invalidation_projection(
        operation, legacy_result
    )
    terminal_prewrite_noop = _is_terminal_prewrite_noop(
        mutation_state, reason_code, legacy_result
    )
    followup_required = mutation_state != "verified" and not terminal_prewrite_noop

    result: dict[str, Any] = {
        "contract": "jarvis.execution_result.v1",
        "operation": operation,
        "target": _target(request),
        "feature": {
            "id": _bounded_text(legacy_result.get("feature_id"), 128),
            "name": _bounded_text(legacy_result.get("feature_name")),
            "type": _bounded_text(legacy_result.get("feature_type"), 128),
        },
        "requested_parameters": canonical_request(operation, request),
        "mutation": {
            "state": mutation_state,
            "changed": legacy_result.get("changed"),
            "transport_ok": legacy_result.get("transport_ok"),
            "http_ok": legacy_result.get("http_ok"),
            "reason_code": reason_code,
            "verification_scope": _bounded_text(
                legacy_result.get("verification_scope"), 128
            ),
        },
        "regeneration": {
            "state": _bounded_text(legacy_result.get("status"), 64) or "UNKNOWN",
            "ok": legacy_result.get("regen_ok"),
        },
        "invalidated_state_categories": invalidated_categories,
        "invalidation": invalidation,
        "followup": {
            "required": followup_required,
            "reason": (
                "requested_state_verified"
                if mutation_state == "verified"
                else "requested_state_already_satisfied"
                if terminal_prewrite_noop
                else (reason_code or f"mutation_{mutation_state}").casefold()
            ),
        },
        "response_mode": response_mode,
    }

    changes = legacy_result.get("changes")
    if isinstance(changes, Mapping):
        result["change_summary"] = _compact_changes(changes)

    warnings = legacy_result.get("warnings")
    if isinstance(warnings, list) and warnings:
        result["warnings"] = [_bounded_text(item, 512) for item in warnings[:8]]
    notes = legacy_result.get("notes")
    if isinstance(notes, list) and notes:
        result["notes"] = [_bounded_text(item, 512) for item in notes[:8]]

    if followup_required:
        result["blocker"] = {
            "reason_code": reason_code,
            "message": error_message,
            "recovery_hint": recovery_hint,
        }

    if response_mode == "diagnostic":
        # The delegated handler has already passed its private/raw boundary.
        # Retaining that public result preserves existing diagnostic evidence.
        result["diagnostic"] = {"legacy_result": dict(legacy_result)}

    return result


def _compact_feature_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    raw_parameters = detail.get("parameters")
    if isinstance(raw_parameters, list):
        for parameter in raw_parameters:
            if not isinstance(parameter, Mapping):
                continue
            parameter_id = _bounded_text(parameter.get("parameterId"), 128)
            if not parameter_id:
                continue
            parameters[parameter_id] = parameter.get("value_summary")

    reference_summary = detail.get("reference_summary")
    reference_summary = (
        reference_summary if isinstance(reference_summary, Mapping) else {}
    )
    warnings = detail.get("warnings")
    return {
        "ordinal": detail.get("ordinal"),
        "feature_id": _bounded_text(detail.get("featureId"), 128),
        "name": _bounded_text(detail.get("name")),
        "type": _bounded_text(
            detail.get("featureType") or detail.get("btType"), 128
        ),
        "status": _bounded_text(detail.get("status"), 64) or "UNKNOWN",
        "suppressed": bool(detail.get("suppressed")),
        "parameter_count": detail.get("parameter_count", 0),
        "canonical_parameters": parameters,
        "references": {
            "confirmed_feature_count": reference_summary.get(
                "confirmed_feature_count", 0
            ),
            "unresolved_reference_count": reference_summary.get(
                "unresolved_reference_count", 0
            ),
            "evidence_types": list(reference_summary.get("evidenceTypes") or []),
        },
        "truncated": bool(detail.get("truncated")),
        "warnings": [
            _bounded_text(warning, 256)
            for warning in (warnings if isinstance(warnings, list) else [])[:8]
        ],
    }


def compact_feature(
    features_doc: Mapping[str, Any],
    feature_id: str,
    *,
    include_parameters: bool = True,
    max_parameters: int = 12,
) -> dict[str, Any]:
    """Return one normalized feature from one authoritative feature document."""

    index = FeatureIndex.build(features_doc)
    detail = index.inspect(
        feature_id,
        include_parameters=include_parameters,
        max_parameters=max_parameters,
    )
    return _compact_feature_detail(detail)


def _regeneration_state(index: FeatureIndex) -> str:
    statuses = {record.status.upper() for record in index.records}
    if not statuses:
        return "EMPTY"
    if "ERROR" in statuses:
        return "ERROR"
    if "WARNING" in statuses:
        return "WARNING"
    if statuses <= _SUCCESSFUL_REGEN:
        return "OK"
    return "UNKNOWN"


def compact_model_state(
    *,
    target: Mapping[str, Any],
    features_doc: Mapping[str, Any],
    parts: Sequence[Mapping[str, Any]],
    recent_feature_limit: int = 20,
    max_parameters_per_feature: int = 8,
    changed_feature_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a structured state summary without copying raw feature/topology trees."""

    index = FeatureIndex.build(features_doc)
    feature_limit = max(1, min(int(recent_feature_limit), 50))
    parameter_limit = max(1, min(int(max_parameters_per_feature), 16))
    selected_records = index.records[-feature_limit:]
    feature_rows = [
        _compact_feature_detail(
            index.inspect(record.feature_id, max_parameters=parameter_limit)
        )
        for record in selected_records
    ]
    all_failures = [
        {
            "feature_id": _bounded_text(record.feature_id, 128),
            "name": _bounded_text(record.name),
            "status": _bounded_text(record.status, 64),
        }
        for record in index.records
        if record.status.upper() in {"ERROR", "WARNING"}
    ]
    failure_rows = all_failures[:_MAX_FAILURE_ROWS]

    part_rows = [
        {
            "body_id": _bounded_text(part.get("partId") or part.get("id"), 128),
            "name": _bounded_text(part.get("name")),
            "type": _bounded_text(
                part.get("partType") or part.get("bodyType") or part.get("type"), 128
            ),
        }
        for part in list(parts)[:_MAX_BODIES]
        if isinstance(part, Mapping)
    ]
    known_ids = {record.feature_id for record in index.records}
    changed = [
        _bounded_text(feature_id, 128)
        for feature_id in list(changed_feature_ids)[:_MAX_REQUEST_ITEMS]
        if feature_id in known_ids
    ]

    unknown_or_stale = [
        {
            "category": "topology",
            "state": "not_loaded",
            "reason": "Compact model state does not read body topology.",
        }
    ]
    if any("sketch" in (record.feature_type or record.bt_type).casefold() for record in index.records):
        unknown_or_stale.append(
            {
                "category": "sketch_constraints",
                "state": "unavailable",
                "reason": "No authoritative compact constraint status was present.",
            }
        )

    revision = features_doc.get("sourceMicroversion")
    return {
        "contract": "jarvis.compact_model_state.v1",
        "target": _target(target),
        "source_revision": revision if isinstance(revision, str) and revision else None,
        "regeneration": {
            "state": _regeneration_state(index),
            "feature_count": len(index.records),
            "failure_count": len(all_failures),
        },
        "bodies": {
            "count": len(parts),
            "returned": len(part_rows),
            "truncated": len(part_rows) < len(parts),
            "rows": part_rows,
        },
        "features": {
            "total": len(index.records),
            "returned": len(feature_rows),
            "truncated": len(feature_rows) < len(index.records),
            "rows": feature_rows,
        },
        "failures": {
            "total": len(all_failures),
            "returned": len(failure_rows),
            "truncated": len(failure_rows) < len(all_failures),
            "rows": failure_rows,
        },
        "changed_feature_ids": changed,
        "unknown_or_stale": unknown_or_stale,
    }


_MUTATION_TOOLS = SUPPORTED_EXECUTION_OPERATIONS | frozenset(
    {
        "create_sketch_rectangle",
        "create_rounded_rectangle_sketch",
        "create_sketch_circle",
        "create_sketch_line",
        "create_sketch_arc",
        "create_fillet",
        "create_thicken",
        "create_offset_plane",
        "create_circular_pattern",
        "create_boolean",
        "edit_sketch",
        "write_featurescript_feature",
        "delete_feature",
        "delete_feature_by_name",
    }
)
_VERIFICATION_PROXY_TOOLS = frozenset(
    {"describe_part_studio", "get_compact_model_state"}
)
_RESOLUTION_PROXY_TOOLS = frozenset(
    {
        "list_entities",
        "get_features",
        "start_model_context",
        "get_feature_tree_compact",
        "find_features",
        "inspect_feature",
        "inspect_feature_compact",
    }
)


def _target_key(arguments: Mapping[str, Any]) -> tuple[str, str, str] | None:
    nested = arguments.get("request")
    source = nested if isinstance(nested, Mapping) else arguments
    values = tuple(source.get(key) for key in ("documentId", "workspaceId", "elementId"))
    if all(isinstance(value, str) and value for value in values):
        return values  # type: ignore[return-value]
    return None


def _family(tool_name: str) -> str:
    if tool_name == "execute_feature" or tool_name in _MUTATION_TOOLS:
        return "mutation"
    if tool_name in _VERIFICATION_PROXY_TOOLS:
        return "verification"
    if tool_name in _RESOLUTION_PROXY_TOOLS:
        return "inspection"
    return "other"


class ExecutionProtocolMetrics:
    """Small process-local MCP round-trip and payload measurement helper."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls = 0
        self._logical_operations = 0
        self._response_bytes = 0
        self._largest_tool: str | None = None
        self._largest_bytes = 0
        self._verification_followup_proxy = 0
        self._target_resolution_proxy = 0
        self._pending_mutation_targets: set[tuple[str, str, str]] = set()
        self._by_family: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "response_bytes": 0, "max_response_bytes": 0}
        )
        self._by_response_mode: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "response_bytes": 0, "max_response_bytes": 0}
        )

    def record(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        response_bytes: int,
    ) -> None:
        safe_bytes = max(0, int(response_bytes))
        family = _family(tool_name)
        target_key = _target_key(arguments)
        response_mode = "legacy"
        if tool_name == "execute_feature":
            requested_mode = arguments.get("responseMode", "compact")
            response_mode = (
                requested_mode
                if requested_mode in {"compact", "diagnostic"}
                else "unknown"
            )
        with self._lock:
            self._calls += 1
            self._response_bytes += safe_bytes
            family_row = self._by_family[family]
            family_row["calls"] += 1
            family_row["response_bytes"] += safe_bytes
            family_row["max_response_bytes"] = max(
                family_row["max_response_bytes"], safe_bytes
            )
            mode_row = self._by_response_mode[response_mode]
            mode_row["calls"] += 1
            mode_row["response_bytes"] += safe_bytes
            mode_row["max_response_bytes"] = max(
                mode_row["max_response_bytes"], safe_bytes
            )
            if safe_bytes > self._largest_bytes:
                self._largest_tool = tool_name
                self._largest_bytes = safe_bytes

            if family == "mutation":
                self._logical_operations += 1
                if target_key is not None:
                    self._pending_mutation_targets.add(target_key)
            elif (
                tool_name in _VERIFICATION_PROXY_TOOLS
                and target_key is not None
                and target_key in self._pending_mutation_targets
            ):
                self._verification_followup_proxy += 1
                self._pending_mutation_targets.discard(target_key)

            if tool_name in _RESOLUTION_PROXY_TOOLS:
                self._target_resolution_proxy += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "contract": "jarvis.execution_protocol_metrics.v1",
                "mcp_tool_invocations": self._calls,
                "model_tool_round_trip_proxy": self._calls,
                "logical_operations": self._logical_operations,
                "response_bytes": self._response_bytes,
                "largest_response": {
                    "tool": self._largest_tool,
                    "bytes": self._largest_bytes,
                },
                "verification_followup_proxy": self._verification_followup_proxy,
                "target_resolution_call_proxy": self._target_resolution_proxy,
                "by_family": {
                    family: dict(values)
                    for family, values in sorted(self._by_family.items())
                },
                "by_response_mode": {
                    mode: dict(values)
                    for mode, values in sorted(self._by_response_mode.items())
                },
                "proxy_definitions": {
                    "model_tool_round_trip_proxy": (
                        "one counted outer MCP tool invocation equals one round-trip proxy"
                    ),
                    "verification_followup_proxy": (
                        "describe_part_studio/get_compact_model_state called on a target "
                        "after a measured mutation"
                    ),
                    "target_resolution_call_proxy": (
                        "calls to feature/entity discovery and compact inspection tools"
                    ),
                },
            }
