"""Bounded, cache-only intelligence for existing Part Studio feature payloads."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional


MAX_FEATURE_RESULTS = 50
MAX_INSPECTION_PARAMETERS = 80
MAX_DEPENDENCY_ROWS = 50
MAX_SLICE_DEPTH = 3
MAX_SLICE_NODES = 80
MAX_SLICE_EDGES = 160
_MAX_TEXT = 256
_MAX_REFERENCE_DEPTH = 6
_MAX_REFERENCE_LIST_ITEMS = 64
_MAX_UNRESOLVED_PER_FEATURE = 100
_MAX_LIST_SUMMARY_ITEMS = 8

EvidenceType = Literal[
    "EXACT_FEATURE_ID",
    "EXPLICIT_QUERY_REFERENCE",
    "UNRESOLVED_GEOMETRY_REFERENCE",
    "UNKNOWN_REFERENCE",
]


class FeatureNotFoundError(LookupError):
    """Raised when a cached index does not contain the requested feature ID."""

    def __init__(self, feature_id: str):
        super().__init__(f"Feature not found: {feature_id}")
        self.feature_id = feature_id


@dataclass(frozen=True)
class FeatureRecord:
    """Normalized searchable metadata; never contains a raw feature subtree."""

    ordinal: int
    feature_id: str
    name: str
    feature_type: str
    bt_type: str
    status: str
    suppressed: bool
    parameter_count: int
    parameter_ids: tuple[str, ...]
    raw_index: int
    search_text: str


@dataclass(frozen=True)
class DependencyEdge:
    source_feature_id: str
    target_feature_id: str
    evidence_type: Literal["EXACT_FEATURE_ID", "EXPLICIT_QUERY_REFERENCE"]
    parameter_id: str


def _bounded_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}\u2026"


def _feature_dicts(raw_features: Any) -> list[dict[str, Any]]:
    if isinstance(raw_features, dict):
        candidate = raw_features.get("features", [])
    elif isinstance(raw_features, list):
        candidate = raw_features
    else:
        candidate = []
    if not isinstance(candidate, list):
        return []
    return [feature for feature in candidate if isinstance(feature, dict)]


def _feature_states(raw_features: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_features, dict):
        return {}
    candidate = raw_features.get("featureStates", {})
    if not isinstance(candidate, dict):
        return {}
    return {str(key): value for key, value in candidate.items() if isinstance(value, dict)}


def _parameter_dicts(feature: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = feature.get("parameters", [])
    if not isinstance(parameters, list):
        return []
    return [parameter for parameter in parameters if isinstance(parameter, dict)]


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return "unsupported value"


def _summarize_list(value: list[Any]) -> dict[str, Any]:
    returned = min(len(value), _MAX_LIST_SUMMARY_ITEMS)
    return {
        "kind": "list",
        "count": len(value),
        "items": [_safe_scalar(item) for item in value[:returned]],
        "truncated": returned < len(value),
    }


def _edge_priority(evidence_type: str) -> int:
    return 2 if evidence_type == "EXACT_FEATURE_ID" else 1


class FeatureIndex:
    """One normalized feature/dependency index built from a cached Onshape payload."""

    def __init__(
        self,
        *,
        records: tuple[FeatureRecord, ...],
        raw_features: tuple[dict[str, Any], ...],
        upstream: dict[str, tuple[DependencyEdge, ...]],
        downstream: dict[str, tuple[DependencyEdge, ...]],
        unresolved: dict[str, tuple[dict[str, Any], ...]],
    ):
        self.records = records
        self._raw_features = raw_features
        self._by_id = {record.feature_id: record for record in records if record.feature_id}
        self._upstream = upstream
        self._downstream = downstream
        self._unresolved = unresolved

    @classmethod
    def build(cls, raw_features: Any) -> "FeatureIndex":
        """Normalize once without performing an external read or stringifying raw JSON."""

        raw_rows = _feature_dicts(raw_features)
        states = _feature_states(raw_features)
        records: list[FeatureRecord] = []

        for raw_index, feature in enumerate(raw_rows):
            feature_id = _bounded_text(feature.get("featureId", ""), 128)
            raw_name = feature.get("name", "")
            name = raw_name if isinstance(raw_name, str) else ""
            parameters = _parameter_dicts(feature)
            parameter_ids = tuple(
                _bounded_text(parameter.get("parameterId", ""), 128) for parameter in parameters
            )
            feature_type = _bounded_text(
                feature.get("featureType") or feature.get("typeName") or "", 128
            )
            bt_type = _bounded_text(feature.get("btType", ""), 128)
            status = _bounded_text(
                states.get(feature_id, {}).get("featureStatus") or "UNKNOWN", 64
            )
            search_terms = [
                feature_id,
                _bounded_text(name),
                feature_type,
                bt_type,
                *parameter_ids,
            ]
            for parameter in parameters:
                value = parameter.get("value")
                expression = parameter.get("expression")
                if isinstance(value, str):
                    search_terms.append(_bounded_text(value))
                if isinstance(expression, str):
                    search_terms.append(_bounded_text(expression))

            records.append(
                FeatureRecord(
                    ordinal=raw_index + 1,
                    feature_id=feature_id,
                    name=name,
                    feature_type=feature_type,
                    bt_type=bt_type,
                    status=status,
                    suppressed=bool(feature.get("suppressed")),
                    parameter_count=len(parameters),
                    parameter_ids=parameter_ids,
                    raw_index=raw_index,
                    search_text=" ".join(search_terms).casefold(),
                )
            )

        record_tuple = tuple(records)
        by_id = {record.feature_id: record for record in record_tuple if record.feature_id}
        known_ids = frozenset(by_id)

        def ordinal_key(feature_id: str) -> tuple[int, str]:
            return (
                by_id[feature_id].ordinal if feature_id in by_id else 2**31,
                feature_id,
            )

        upstream: dict[str, tuple[DependencyEdge, ...]] = {}
        unresolved: dict[str, tuple[dict[str, Any], ...]] = {}

        for record in record_tuple:
            selected_edges: dict[str, DependencyEdge] = {}
            unresolved_rows: list[dict[str, Any]] = []
            for parameter in _parameter_dicts(raw_rows[record.raw_index]):
                parameter_edges, parameter_unresolved = _extract_parameter_references(
                    parameter,
                    source_feature_id=record.feature_id,
                    known_feature_ids=known_ids,
                )
                for edge in parameter_edges:
                    existing = selected_edges.get(edge.target_feature_id)
                    if existing is None or _edge_priority(edge.evidence_type) > _edge_priority(
                        existing.evidence_type
                    ):
                        selected_edges[edge.target_feature_id] = edge
                unresolved_rows.extend(parameter_unresolved)

            upstream[record.feature_id] = tuple(
                sorted(
                    selected_edges.values(),
                    key=lambda edge: (*ordinal_key(edge.target_feature_id), edge.evidence_type),
                )
            )
            unresolved[record.feature_id] = tuple(
                _dedupe_dicts(unresolved_rows)[:_MAX_UNRESOLVED_PER_FEATURE]
            )

        reverse: dict[str, list[DependencyEdge]] = {feature_id: [] for feature_id in by_id}
        for edges in upstream.values():
            for edge in edges:
                reverse.setdefault(edge.target_feature_id, []).append(edge)
        downstream = {
            feature_id: tuple(
                sorted(
                    edges,
                    key=lambda edge: (*ordinal_key(edge.source_feature_id), edge.evidence_type),
                )
            )
            for feature_id, edges in reverse.items()
        }
        return cls(
            records=record_tuple,
            raw_features=tuple(raw_rows),
            upstream=upstream,
            downstream=downstream,
            unresolved=unresolved,
        )

    def get_record(self, feature_id: str) -> FeatureRecord:
        try:
            return self._by_id[feature_id]
        except KeyError as exc:
            raise FeatureNotFoundError(_bounded_text(feature_id, 128)) from exc

    def search(
        self,
        *,
        query: Optional[str] = None,
        feature_type: Optional[str] = None,
        status: Optional[str] = None,
        suppressed: Optional[bool] = None,
        from_ordinal: Optional[int] = None,
        to_ordinal: Optional[int] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return a stable L1-sized result set from normalized metadata only."""

        wanted_query = query.casefold() if isinstance(query, str) and query else None
        wanted_type = feature_type.casefold() if feature_type else None
        wanted_status = status.casefold() if status else None
        first = max(1, int(from_ordinal)) if from_ordinal is not None else None
        last = max(1, int(to_ordinal)) if to_ordinal is not None else None
        capped_limit = max(1, min(int(limit), MAX_FEATURE_RESULTS))
        matches: list[FeatureRecord] = []

        for record in self.records:
            if wanted_query and wanted_query not in record.search_text:
                continue
            if wanted_type and record.feature_type.casefold() != wanted_type:
                continue
            if wanted_status and record.status.casefold() != wanted_status:
                continue
            if suppressed is not None and record.suppressed is not suppressed:
                continue
            if first is not None and record.ordinal < first:
                continue
            if last is not None and record.ordinal > last:
                continue
            matches.append(record)

        rows = [self._search_row(record) for record in matches[:capped_limit]]
        return {
            "total": len(matches),
            "returned": len(rows),
            "has_more": len(rows) < len(matches),
            "rows": rows,
        }

    def compact_page(
        self,
        *,
        offset: int = 0,
        limit: int = 25,
        status: Optional[str] = None,
        query: Optional[str] = None,
    ) -> dict[str, Any]:
        """WP-001-compatible page shape backed by the shared normalized index."""

        wanted_query = query.casefold() if isinstance(query, str) and query else None
        wanted_status = status.casefold() if status else None
        matches = [
            record
            for record in self.records
            if (not wanted_query or wanted_query in record.search_text)
            and (not wanted_status or record.status.casefold() == wanted_status)
        ]
        safe_offset = max(0, int(offset))
        capped_limit = max(1, min(int(limit), MAX_FEATURE_RESULTS))
        selected = matches[safe_offset : safe_offset + capped_limit]
        rows = [
            {
                "ordinal": record.ordinal,
                "featureId": record.feature_id,
                "name": record.name,
                "featureType": record.feature_type or record.bt_type,
                "status": record.status,
                "suppressed": record.suppressed,
            }
            for record in selected
        ]
        return {
            "total": len(matches),
            "offset": safe_offset,
            "returned": len(rows),
            "has_more": safe_offset + len(rows) < len(matches),
            "rows": rows,
        }

    def inspect(
        self,
        feature_id: str,
        *,
        include_parameters: bool = True,
        max_parameters: int = 40,
    ) -> dict[str, Any]:
        record = self.get_record(feature_id)
        raw_feature = self._raw_features[record.raw_index]
        raw_parameters = _parameter_dicts(raw_feature)
        capped_parameters = max(1, min(int(max_parameters), MAX_INSPECTION_PARAMETERS))
        selected = raw_parameters[:capped_parameters] if include_parameters else []
        parameters = [
            self._normalize_parameter(parameter, source_feature_id=record.feature_id)
            for parameter in selected
        ]
        unsupported_count = sum(
            parameter.get("value_summary") == "unsupported parameter type"
            for parameter in parameters
        )
        truncated = include_parameters and len(parameters) < len(raw_parameters)
        warnings = []
        if truncated:
            warnings.append("Parameter list truncated at the requested hard-bounded limit")
        if unsupported_count:
            warnings.append(f"{unsupported_count} unsupported parameter type(s) kept compact")
        if not include_parameters and raw_parameters:
            warnings.append("Parameters omitted by request")

        return {
            "ordinal": record.ordinal,
            "featureId": record.feature_id,
            "name": record.name,
            "featureType": record.feature_type,
            "btType": record.bt_type,
            "status": record.status,
            "suppressed": record.suppressed,
            "parameter_count": len(raw_parameters),
            "returned_parameters": len(parameters),
            "parameters": parameters,
            "truncated": truncated,
            "reference_summary": self._feature_reference_summary(record.feature_id),
            "warnings": warnings,
        }

    def dependencies(self, feature_id: str, *, limit: int = 30) -> dict[str, Any]:
        record = self.get_record(feature_id)
        capped_limit = max(1, min(int(limit), MAX_DEPENDENCY_ROWS))
        upstream_edges = self._upstream.get(feature_id, ())
        downstream_edges = self._downstream.get(feature_id, ())
        unresolved = self._unresolved.get(feature_id, ())
        upstream = [
            self._dependency_row(edge.target_feature_id, edge.evidence_type)
            for edge in upstream_edges[:capped_limit]
        ]
        downstream = [
            self._dependency_row(edge.source_feature_id, edge.evidence_type)
            for edge in downstream_edges[:capped_limit]
        ]
        unresolved_rows = [dict(row) for row in unresolved[:capped_limit]]
        truncated = (
            len(upstream) < len(upstream_edges)
            or len(downstream) < len(downstream_edges)
            or len(unresolved_rows) < len(unresolved)
        )
        return {
            "feature": self._node_row(record),
            "upstream": upstream,
            "downstream": downstream,
            "unresolved_references": unresolved_rows,
            "completeness": {
                "status": "PARTIAL" if unresolved else "KNOWN_REFERENCES_ONLY",
                "absence_proves_independence": False,
                "limitation": (
                    "Only explicit cached feature references become edges; unresolved geometry "
                    "references may hide additional dependencies."
                ),
            },
            "truncated": truncated,
        }

    def dependency_slice(
        self,
        feature_id: str,
        *,
        direction: Literal["upstream", "downstream", "both"] = "both",
        depth: int = 1,
        max_nodes: int = 40,
        max_edges: int = 80,
    ) -> dict[str, Any]:
        root = self.get_record(feature_id)
        if direction not in {"upstream", "downstream", "both"}:
            raise ValueError("direction must be upstream, downstream, or both")
        capped_depth = max(1, min(int(depth), MAX_SLICE_DEPTH))
        capped_nodes = max(1, min(int(max_nodes), MAX_SLICE_NODES))
        capped_edges = max(1, min(int(max_edges), MAX_SLICE_EDGES))
        visited = {feature_id: 0}
        pending: deque[str] = deque([feature_id])
        selected_edges: dict[tuple[str, str], DependencyEdge] = {}
        truncated = False

        while pending:
            current = pending.popleft()
            current_depth = visited[current]
            if current_depth >= capped_depth:
                continue
            candidates = self._traversal_candidates(current, direction)
            for neighbor, edge in candidates:
                edge_key = (edge.source_feature_id, edge.target_feature_id)
                if edge_key not in selected_edges and len(selected_edges) >= capped_edges:
                    truncated = True
                    continue
                if neighbor not in visited:
                    if len(visited) >= capped_nodes:
                        truncated = True
                        continue
                    visited[neighbor] = current_depth + 1
                    pending.append(neighbor)
                selected_edges.setdefault(edge_key, edge)

        node_records = sorted(
            (self.get_record(node_id) for node_id in visited),
            key=lambda record: (record.ordinal, record.feature_id),
        )
        edges = sorted(
            selected_edges.values(),
            key=lambda edge: (
                self.get_record(edge.source_feature_id).ordinal,
                self.get_record(edge.target_feature_id).ordinal,
                edge.evidence_type,
            ),
        )
        unresolved_total = sum(len(self._unresolved.get(record.feature_id, ())) for record in node_records)
        unresolved_features = sum(bool(self._unresolved.get(record.feature_id)) for record in node_records)
        nodes = [self._node_row(record) for record in node_records]
        edge_rows = [
            {
                "fromFeatureId": edge.source_feature_id,
                "toFeatureId": edge.target_feature_id,
                "evidenceType": edge.evidence_type,
            }
            for edge in edges
        ]
        return {
            "root": self._node_row(root),
            "nodes": nodes,
            "edges": edge_rows,
            "unresolved_summary": {
                "reference_count": unresolved_total,
                "feature_count": unresolved_features,
                "limitation": (
                    "Unresolved geometry references are counted but never converted to guessed "
                    "feature edges."
                ),
            },
            "truncated": truncated,
            "limits": {
                "depth": capped_depth,
                "maxNodes": capped_nodes,
                "maxEdges": capped_edges,
            },
        }

    def _search_row(self, record: FeatureRecord) -> dict[str, Any]:
        return {
            "ordinal": record.ordinal,
            "featureId": record.feature_id,
            "name": record.name,
            "featureType": record.feature_type,
            "btType": record.bt_type,
            "status": record.status,
            "suppressed": record.suppressed,
            "parameterCount": record.parameter_count,
        }

    def _node_row(self, record: FeatureRecord) -> dict[str, Any]:
        return {
            "featureId": record.feature_id,
            "ordinal": record.ordinal,
            "name": record.name,
            "type": record.feature_type or record.bt_type,
            "status": record.status,
            "unresolvedReferenceCount": len(self._unresolved.get(record.feature_id, ())),
        }

    def _dependency_row(self, feature_id: str, evidence_type: str) -> dict[str, Any]:
        record = self.get_record(feature_id)
        return {
            "featureId": record.feature_id,
            "ordinal": record.ordinal,
            "name": record.name,
            "type": record.feature_type or record.bt_type,
            "status": record.status,
            "evidenceType": evidence_type,
        }

    def _feature_reference_summary(self, feature_id: str) -> dict[str, Any]:
        upstream = self._upstream.get(feature_id, ())
        unresolved = self._unresolved.get(feature_id, ())
        return {
            "confirmed_feature_count": len(upstream),
            "unresolved_reference_count": len(unresolved),
            "evidenceTypes": sorted({edge.evidence_type for edge in upstream}),
        }

    def _normalize_parameter(
        self, parameter: dict[str, Any], *, source_feature_id: str
    ) -> dict[str, Any]:
        parameter_id = _bounded_text(parameter.get("parameterId", ""), 128)
        parameter_type = _bounded_text(parameter.get("btType", "UNKNOWN"), 128)
        base: dict[str, Any] = {
            "parameterId": parameter_id,
            "parameterType": parameter_type,
        }
        type_folded = parameter_type.casefold()

        if "query" in type_folded:
            queries = parameter.get("queries", [])
            query_count = len(queries) if isinstance(queries, list) else 0
            edges, unresolved = _extract_parameter_references(
                parameter,
                source_feature_id=source_feature_id,
                known_feature_ids=frozenset(self._by_id),
            )
            base["value_summary"] = {"query_count": query_count}
            base["reference_summary"] = {
                "confirmed": [
                    {
                        "featureId": edge.target_feature_id,
                        "evidenceType": edge.evidence_type,
                    }
                    for edge in edges[:MAX_DEPENDENCY_ROWS]
                ],
                "unresolved_count": len(unresolved),
            }
            return base

        if "quantity" in type_folded:
            summary: dict[str, Any] = {}
            for key in ("expression", "value", "units"):
                if key in parameter:
                    summary[key] = _safe_scalar(parameter[key])
            base["value_summary"] = summary
            return base

        if any(token in type_folded for token in ("enum", "string", "boolean")):
            base["value_summary"] = _safe_scalar(parameter.get("value"))
            return base

        if "feature" in type_folded:
            value = parameter.get("value", parameter.get("featureId"))
            base["value_summary"] = _safe_scalar(value)
            edges, unresolved = _extract_parameter_references(
                parameter,
                source_feature_id=source_feature_id,
                known_feature_ids=frozenset(self._by_id),
            )
            base["reference_summary"] = {
                "confirmed": [
                    {
                        "featureId": edge.target_feature_id,
                        "evidenceType": edge.evidence_type,
                    }
                    for edge in edges[:MAX_DEPENDENCY_ROWS]
                ],
                "unresolved_count": len(unresolved),
            }
            return base

        if any(token in type_folded for token in ("array", "list")):
            value = parameter.get("value", parameter.get("items", []))
            base["value_summary"] = _summarize_list(value if isinstance(value, list) else [])
            return base

        if any(token in type_folded for token in ("integer", "double", "real", "value")):
            base["value_summary"] = _safe_scalar(parameter.get("value"))
            return base

        base.update(
            {
                "value_summary": "unsupported parameter type",
                "raw_available": True,
            }
        )
        return base

    def _traversal_candidates(
        self, feature_id: str, direction: str
    ) -> list[tuple[str, DependencyEdge]]:
        candidates: list[tuple[str, DependencyEdge]] = []
        if direction in {"upstream", "both"}:
            candidates.extend(
                (edge.target_feature_id, edge) for edge in self._upstream.get(feature_id, ())
            )
        if direction in {"downstream", "both"}:
            candidates.extend(
                (edge.source_feature_id, edge) for edge in self._downstream.get(feature_id, ())
            )
        candidates.sort(
            key=lambda item: (
                self.get_record(item[0]).ordinal,
                item[0],
                item[1].source_feature_id,
                item[1].target_feature_id,
            )
        )
        return candidates


def _dedupe_dicts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, Any], ...]] = set()
    result = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _extract_parameter_references(
    parameter: dict[str, Any],
    *,
    source_feature_id: str,
    known_feature_ids: frozenset[str],
) -> tuple[list[DependencyEdge], list[dict[str, Any]]]:
    """Extract only explicit feature ownership and bounded unresolved query evidence."""

    parameter_id = _bounded_text(parameter.get("parameterId", ""), 128)
    selected_edges: dict[str, DependencyEdge] = {}
    unresolved: list[dict[str, Any]] = []

    def add_edge(target: str, evidence_type: str) -> None:
        edge = DependencyEdge(
            source_feature_id=source_feature_id,
            target_feature_id=target,
            evidence_type=evidence_type,  # type: ignore[arg-type]
            parameter_id=parameter_id,
        )
        existing = selected_edges.get(target)
        if existing is None or _edge_priority(edge.evidence_type) > _edge_priority(
            existing.evidence_type
        ):
            selected_edges[target] = edge

    def add_unresolved(
        evidence_type: EvidenceType,
        reference_type: str,
        reason: str,
        identifier_count: int,
    ) -> None:
        if len(unresolved) >= _MAX_UNRESOLVED_PER_FEATURE:
            return
        unresolved.append(
            {
                "parameterId": parameter_id,
                "evidenceType": evidence_type,
                "referenceType": _bounded_text(reference_type, 128) or "UNKNOWN",
                "reason": reason,
                "identifierCount": max(0, identifier_count),
            }
        )

    def visit(value: Any, *, depth: int, query_context: bool = False) -> None:
        if depth > _MAX_REFERENCE_DEPTH:
            add_unresolved(
                "UNKNOWN_REFERENCE",
                "BOUNDED_TRAVERSAL",
                "Reference traversal depth limit reached",
                0,
            )
            return
        if isinstance(value, list):
            for item in value[:_MAX_REFERENCE_LIST_ITEMS]:
                visit(item, depth=depth + 1, query_context=query_context)
            if len(value) > _MAX_REFERENCE_LIST_ITEMS:
                add_unresolved(
                    "UNKNOWN_REFERENCE",
                    "BOUNDED_TRAVERSAL",
                    "Reference list traversal limit reached",
                    len(value) - _MAX_REFERENCE_LIST_ITEMS,
                )
            return
        if not isinstance(value, dict):
            return

        reference_type = _bounded_text(value.get("btType", ""), 128)
        is_query = query_context or "query" in reference_type.casefold()
        local_has_explicit = False

        explicit_feature_id = value.get("featureId")
        if isinstance(explicit_feature_id, str) and explicit_feature_id:
            local_has_explicit = True
            if explicit_feature_id in known_feature_ids:
                add_edge(
                    explicit_feature_id,
                    "EXPLICIT_QUERY_REFERENCE" if is_query else "EXACT_FEATURE_ID",
                )
            else:
                add_unresolved(
                    "UNKNOWN_REFERENCE",
                    reference_type,
                    "Explicit feature ID is not present in the cached feature index",
                    1,
                )

        explicit_feature_ids = value.get("featureIds")
        if isinstance(explicit_feature_ids, list):
            candidates = [item for item in explicit_feature_ids if isinstance(item, str) and item]
            if candidates:
                local_has_explicit = True
            for candidate in candidates[:_MAX_REFERENCE_LIST_ITEMS]:
                if candidate in known_feature_ids:
                    add_edge(
                        candidate,
                        "EXPLICIT_QUERY_REFERENCE" if is_query else "EXACT_FEATURE_ID",
                    )
                else:
                    add_unresolved(
                        "UNKNOWN_REFERENCE",
                        reference_type,
                        "Explicit feature ID is not present in the cached feature index",
                        1,
                    )

        if "feature" in reference_type.casefold() and not is_query:
            feature_value = value.get("value")
            candidates = feature_value if isinstance(feature_value, list) else [feature_value]
            for candidate in candidates[:_MAX_REFERENCE_LIST_ITEMS]:
                if isinstance(candidate, str) and candidate in known_feature_ids:
                    local_has_explicit = True
                    add_edge(candidate, "EXACT_FEATURE_ID")

        deterministic_ids = value.get("deterministicIds")
        deterministic_count = (
            len(deterministic_ids) if isinstance(deterministic_ids, list) else 0
        )
        has_query_text = any(
            isinstance(value.get(key), str) and bool(value.get(key))
            for key in ("queryString", "queryStatement")
        )
        if is_query and not local_has_explicit and (deterministic_count or has_query_text):
            add_unresolved(
                "UNRESOLVED_GEOMETRY_REFERENCE",
                reference_type,
                "Geometry reference has no proven producing feature",
                deterministic_count or 1,
            )

        for key, child in value.items():
            if isinstance(child, (dict, list)):
                visit(child, depth=depth + 1, query_context=is_query or key == "queries")

    visit(parameter, depth=0)
    edges = sorted(
        selected_edges.values(),
        key=lambda edge: (edge.target_feature_id, edge.evidence_type),
    )
    return edges, _dedupe_dicts(unresolved)
