"""In-memory context storage and feature normalization for the pilot."""

from __future__ import annotations

from collections import Counter
import copy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Optional
from uuid import uuid4

from .models import BudgetPolicy, ModelRef, WorkingState


class ContextNotFoundError(LookupError):
    def __init__(self, context_handle: str):
        super().__init__("Unknown context handle")
        self.context_handle = context_handle


class GovernanceBudgetError(RuntimeError):
    """A bounded governance operation was refused before accessing cached data."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class WorkingStateFieldForbiddenError(ValueError):
    def __init__(self, fields: list[str]):
        super().__init__("Working state update contains fields that clients cannot modify")
        self.fields = fields


@dataclass
class ReadLedger:
    onshape_reads: int = 0
    cache_reads: int = 0
    events: list[str] = field(default_factory=list)
    request_fingerprints: dict[str, int] = field(default_factory=dict)

    def record_onshape_read(self) -> None:
        self.onshape_reads += 1
        self.events.append("onshape:get_features")

    def record_cache_read(self, fingerprint: str) -> bool:
        repeat = fingerprint in self.request_fingerprints
        self.request_fingerprints[fingerprint] = self.request_fingerprints.get(fingerprint, 0) + 1
        self.cache_reads += 1
        self.events.append(f"cache:{fingerprint}")
        return repeat

    def summary(self) -> dict[str, int]:
        return {"onshape_reads": self.onshape_reads, "cache_reads": self.cache_reads}


@dataclass
class StoredContext:
    working_state: WorkingState
    raw_features: Any = None
    cache_metadata: dict[str, Any] = field(
        default_factory=lambda: {
            "state": "empty",
            "feature_count": 0,
            "topology_state": "empty",
            "invalidations": 0,
        }
    )
    ledger: ReadLedger = field(default_factory=ReadLedger)
    feature_index: Any = None


class ContextStore:
    """Small storage boundary whose implementation can later be replaced by SQLite."""

    def __init__(
        self, policy: Optional[BudgetPolicy] = None, *, metrics: Optional[Any] = None
    ):
        self.policy = policy or BudgetPolicy()
        self.metrics = metrics
        self._contexts: dict[str, StoredContext] = {}

    def create(self, document_id: str, workspace_id: str, element_id: str) -> StoredContext:
        handle = str(uuid4())
        record = StoredContext(
            working_state=WorkingState(
                context_handle=handle,
                model_ref=ModelRef(
                    document_id=document_id,
                    workspace_id=workspace_id,
                    element_id=element_id,
                ),
            )
        )
        self._contexts[handle] = record
        return record

    def get(self, context_handle: str) -> StoredContext:
        try:
            return self._contexts[context_handle]
        except KeyError as exc:
            raise ContextNotFoundError(context_handle) from exc

    def delete(self, context_handle: str) -> None:
        self._contexts.pop(context_handle, None)

    def store_features(self, context_handle: str, raw_features: Any) -> StoredContext:
        record = self.get(context_handle)
        record.raw_features = raw_features
        record.working_state.revision_id = extract_revision_id(raw_features)
        record.cache_metadata = {
            "state": "ready",
            "feature_count": len(_features(raw_features)),
            "index_state": "empty",
            "index_builds": 0,
            "topology_state": "unknown",
            "invalidations": 0,
        }
        record.feature_index = None
        return record

    def apply_authoritative_feature_delta(
        self,
        document_id: str,
        workspace_id: str,
        element_id: str,
        authoritative_features: Any,
        *,
        created_feature_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Patch matching cached feature trees from a bounded authoritative reread.

        The reread may contain only the mutated feature. Existing unrelated
        features stay cached, while every normalized index and topology-derived
        fact is invalidated because regeneration can affect downstream geometry.
        """

        delta_features = _features(authoritative_features)
        delta_states = _states(authoritative_features)
        feature_ids = sorted(
            str(feature.get("featureId"))
            for feature in delta_features
            if feature.get("featureId")
        )
        created_ids = set(created_feature_ids or [])
        revision_id = extract_revision_id(authoritative_features)
        updated = 0
        partial_updates = 0
        for record in self._contexts.values():
            model_ref = record.working_state.model_ref
            if (
                model_ref.document_id != document_id
                or model_ref.workspace_id != workspace_id
                or model_ref.element_id != element_id
            ):
                continue
            if not isinstance(record.raw_features, dict):
                continue

            existing_features = list(_features(record.raw_features))
            existing_ids = {
                str(feature.get("featureId"))
                for feature in existing_features
                if feature.get("featureId")
            }
            positions = {
                str(feature.get("featureId")): index
                for index, feature in enumerate(existing_features)
                if feature.get("featureId")
            }
            for feature in delta_features:
                feature_id = str(feature.get("featureId") or "")
                if not feature_id:
                    continue
                copied = copy.deepcopy(feature)
                if feature_id in positions:
                    existing_features[positions[feature_id]] = copied
                else:
                    positions[feature_id] = len(existing_features)
                    existing_features.append(copied)

            existing_states = dict(_states(record.raw_features))
            existing_states.update(copy.deepcopy(delta_states))
            record.raw_features["features"] = existing_features
            record.raw_features["featureStates"] = existing_states
            delta_ids = set(feature_ids)
            complete_snapshot = (
                bool(existing_ids) and existing_ids.issubset(delta_ids)
            ) or (
                bool(delta_ids)
                and delta_ids.issubset(created_ids)
                and existing_ids.isdisjoint(created_ids)
            )
            if revision_id and complete_snapshot:
                record.raw_features["sourceMicroversion"] = revision_id
                record.working_state.revision_id = revision_id
            record.feature_index = None
            record.cache_metadata.update(
                {
                    "state": "ready" if complete_snapshot else "partial",
                    "feature_count": len(existing_features),
                    "index_state": "empty",
                    "index_builds": 0,
                    "topology_state": "stale",
                    "fresh_feature_ids": feature_ids,
                    "delta_revision_id": revision_id,
                    "invalidations": int(
                        record.cache_metadata.get("invalidations", 0)
                    )
                    + 1,
                }
            )
            if self.metrics is not None:
                self.metrics.record_cache("invalidation")
            partial_updates += not complete_snapshot
            updated += 1

        return {
            "contexts_updated": updated,
            "feature_ids": feature_ids,
            "feature_cache": (
                "partially_refreshed"
                if partial_updates
                else "refreshed"
                if feature_ids
                else "unchanged"
            ),
            "topology_cache": "invalidated",
        }

    def invalidate_model(
        self,
        document_id: str,
        workspace_id: str,
        element_id: str,
        *,
        feature_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Mark matching cached evidence stale when no authoritative delta exists."""

        updated = 0
        for record in self._contexts.values():
            model_ref = record.working_state.model_ref
            if (
                model_ref.document_id != document_id
                or model_ref.workspace_id != workspace_id
                or model_ref.element_id != element_id
            ):
                continue
            record.feature_index = None
            record.cache_metadata.update(
                {
                    "state": "stale",
                    "index_state": "empty",
                    "index_builds": 0,
                    "topology_state": "stale",
                    "invalidations": int(
                        record.cache_metadata.get("invalidations", 0)
                    )
                    + 1,
                }
            )
            if self.metrics is not None:
                self.metrics.record_cache("invalidation")
            updated += 1
        return {
            "contexts_updated": updated,
            "feature_ids": sorted(set(feature_ids or [])),
            "feature_cache": "invalidated",
            "topology_cache": "invalidated",
        }

    def replace_authoritative_snapshot(
        self,
        document_id: str,
        workspace_id: str,
        element_id: str,
        authoritative_features: Any,
        *,
        affected_feature_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Replace matching feature caches from a complete authoritative snapshot."""

        updated = 0
        for record in self._contexts.values():
            model_ref = record.working_state.model_ref
            if (
                model_ref.document_id != document_id
                or model_ref.workspace_id != workspace_id
                or model_ref.element_id != element_id
            ):
                continue
            invalidations = int(record.cache_metadata.get("invalidations", 0)) + 1
            record.raw_features = copy.deepcopy(authoritative_features)
            record.working_state.revision_id = extract_revision_id(
                authoritative_features
            )
            record.feature_index = None
            record.cache_metadata = {
                "state": "ready",
                "feature_count": len(_features(authoritative_features)),
                "index_state": "empty",
                "index_builds": 0,
                "topology_state": "stale",
                "invalidations": invalidations,
            }
            if self.metrics is not None:
                self.metrics.record_cache("invalidation")
            updated += 1
        return {
            "contexts_updated": updated,
            "feature_ids": sorted(set(affected_feature_ids or [])),
            "feature_cache": "refreshed",
            "topology_cache": "invalidated",
        }

    def invalidate_workspace(self, document_id: str, workspace_id: str) -> int:
        """Invalidate all cached Part Studios potentially driven by workspace variables."""

        updated = 0
        for record in self._contexts.values():
            model_ref = record.working_state.model_ref
            if (
                model_ref.document_id != document_id
                or model_ref.workspace_id != workspace_id
            ):
                continue
            record.feature_index = None
            record.cache_metadata.update(
                {
                    "state": "stale",
                    "index_state": "empty",
                    "index_builds": 0,
                    "topology_state": "stale",
                    "invalidations": int(
                        record.cache_metadata.get("invalidations", 0)
                    )
                    + 1,
                }
            )
            if self.metrics is not None:
                self.metrics.record_cache("invalidation")
            updated += 1
        return updated

    def mark_topology_fresh(
        self, document_id: str, workspace_id: str, element_id: str
    ) -> int:
        """Record that one authoritative topology snapshot has been obtained."""

        updated = 0
        for record in self._contexts.values():
            model_ref = record.working_state.model_ref
            if (
                model_ref.document_id == document_id
                and model_ref.workspace_id == workspace_id
                and model_ref.element_id == element_id
            ):
                record.cache_metadata["topology_state"] = "fresh"
                updated += 1
        return updated

    def record_onshape_read(self, context_handle: str) -> None:
        self.get(context_handle).ledger.record_onshape_read()

    def get_feature_index(self, context_handle: str) -> tuple[Any, bool]:
        """Build the normalized index once per context and report whether it was a hit."""

        record = self.get(context_handle)
        if record.cache_metadata.get("state") in {"stale", "partial"}:
            raise GovernanceBudgetError(
                "CONTEXT_STATE_STALE",
                "Cached feature state was invalidated and cannot be served as current",
            )
        if record.feature_index is not None:
            if self.metrics is not None:
                self.metrics.record_cache("hit")
            return record.feature_index, True
        from .features import FeatureIndex

        record.feature_index = FeatureIndex.build(record.raw_features)
        if self.metrics is not None:
            self.metrics.record_cache("miss")
        record.cache_metadata["index_state"] = "ready"
        record.cache_metadata["index_builds"] = 1
        return record.feature_index, False

    def begin_operation(self, context_handle: str, *, exploratory_read: bool = False) -> None:
        """Enforce call/read/consecutive gates before a governance tool proceeds."""

        record = self.get(context_handle)
        budget = record.working_state.budget
        if budget.calls_used >= self.policy.max_calls_per_cycle:
            raise GovernanceBudgetError(
                "CALL_BUDGET_EXHAUSTED",
                "The model-context tool-call budget is exhausted",
            )
        if exploratory_read:
            if budget.exploratory_reads_used >= self.policy.max_exploratory_reads_per_cycle:
                raise GovernanceBudgetError(
                    "READ_BUDGET_EXHAUSTED",
                    "The model-context exploratory read budget is exhausted",
                )
            if budget.consecutive_reads >= self.policy.max_consecutive_reads:
                raise GovernanceBudgetError(
                    "HYPOTHESIS_REQUIRED",
                    "Update non-empty focus plus an open hypothesis or concrete next action "
                    "before another exploratory read",
                )
        budget.record_call(exploratory_read=exploratory_read)

    def record_cache_read(
        self,
        context_handle: str,
        *,
        tool_name: str = "get_features",
        scope: Optional[dict[str, Any]] = None,
    ) -> bool:
        fingerprint = _request_fingerprint(tool_name, scope or {})
        return self.get(context_handle).ledger.record_cache_read(fingerprint)

    def update_working_state(
        self, context_handle: str, arguments: dict[str, Any]
    ) -> tuple[StoredContext, bool]:
        allowed = {
            "contextHandle",
            "focusFeatureIds",
            "hypotheses",
            "evidenceRefs",
            "nextAction",
            "phase",
        }
        forbidden = sorted(set(arguments) - allowed)
        if forbidden:
            raise WorkingStateFieldForbiddenError(forbidden)
        record = self.get(context_handle)
        meaningful = record.working_state.apply_operational_update(arguments)
        return record, meaningful

    @staticmethod
    def cache_projection(
        record: StoredContext,
        *,
        index_hit: Optional[bool] = None,
        cache_repeat: Optional[bool] = None,
    ) -> dict[str, Any]:
        projection = dict(record.cache_metadata)
        if index_hit is not None:
            projection["index_hit"] = index_hit
        if cache_repeat is not None:
            projection["cache_repeat"] = cache_repeat
        return projection


def _request_fingerprint(tool_name: str, scope: dict[str, Any]) -> str:
    serialized = json.dumps(scope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(f"{tool_name}:{serialized}".encode("utf-8")).hexdigest()[:16]
    return f"{tool_name}:{digest}"


def _features(raw_features: Any) -> list[dict[str, Any]]:
    if isinstance(raw_features, dict):
        value = raw_features.get("features", [])
    elif isinstance(raw_features, list):
        value = raw_features
    else:
        value = []
    return [feature for feature in value if isinstance(feature, dict)]


def _states(raw_features: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_features, dict):
        return {}
    value = raw_features.get("featureStates", {})
    if not isinstance(value, dict):
        return {}
    return {key: state for key, state in value.items() if isinstance(state, dict)}


def extract_revision_id(raw_features: Any) -> Optional[str]:
    """Use only Onshape's explicit source microversion, never a serialization version."""

    if not isinstance(raw_features, dict):
        return None
    value = raw_features.get("sourceMicroversion")
    return value if isinstance(value, str) and value else None


def summarize_features(raw_features: Any, revision_id: Optional[str]) -> dict[str, Any]:
    features = _features(raw_features)
    states = _states(raw_features)
    status_counts: Counter[str] = Counter()
    error_count = 0
    warning_count = 0

    for feature in features:
        state = states.get(str(feature.get("featureId", "")), {})
        status_value = state.get("featureStatus")
        status = str(status_value).upper() if status_value else None
        if status:
            status_counts[status] += 1

        severities = {status} if status else set()
        messages = state.get("messages", [])
        if isinstance(messages, list):
            severities.update(
                str(message.get("severity", "")).upper()
                for message in messages
                if isinstance(message, dict)
            )
        error_count += "ERROR" in severities
        warning_count += "WARNING" in severities

    return {
        "feature_count": len(features),
        "status_counts": dict(sorted(status_counts.items())),
        "suppressed_count": sum(bool(feature.get("suppressed")) for feature in features),
        "error_count": error_count,
        "warning_count": warning_count,
        "revision_id": revision_id,
    }


def compact_feature_page(
    raw_features: Any,
    *,
    offset: int = 0,
    limit: int = 25,
    status_filter: Optional[str] = None,
    text_query: Optional[str] = None,
) -> dict[str, Any]:
    states = _states(raw_features)
    rows = []
    wanted_status = status_filter.casefold() if status_filter else None
    wanted_text = text_query.casefold() if text_query else None

    for ordinal, feature in enumerate(_features(raw_features), start=1):
        feature_id = str(feature.get("featureId") or "")
        state = states.get(feature_id, {})
        status = str(state.get("featureStatus") or "UNKNOWN")
        feature_type = str(feature.get("featureType") or feature.get("btType") or "")
        row = {
            "ordinal": ordinal,
            "featureId": feature_id,
            "name": str(feature.get("name") or ""),
            "featureType": feature_type,
            "status": status,
            "suppressed": bool(feature.get("suppressed")),
        }
        if wanted_status and status.casefold() != wanted_status:
            continue
        if wanted_text:
            searchable = " ".join((row["featureId"], row["name"], row["featureType"]))
            if wanted_text not in searchable.casefold():
                continue
        rows.append(row)

    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "returned": len(page),
        "has_more": offset + len(page) < total,
        "rows": page,
    }
