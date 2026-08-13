"""In-memory context storage and feature normalization for the pilot."""

from __future__ import annotations

from collections import Counter
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
        default_factory=lambda: {"state": "empty", "feature_count": 0}
    )
    ledger: ReadLedger = field(default_factory=ReadLedger)
    feature_index: Any = None


class ContextStore:
    """Small storage boundary whose implementation can later be replaced by SQLite."""

    def __init__(self, policy: Optional[BudgetPolicy] = None):
        self.policy = policy or BudgetPolicy()
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
        }
        record.feature_index = None
        return record

    def record_onshape_read(self, context_handle: str) -> None:
        self.get(context_handle).ledger.record_onshape_read()

    def get_feature_index(self, context_handle: str) -> tuple[Any, bool]:
        """Build the normalized index once per context and report whether it was a hit."""

        record = self.get(context_handle)
        if record.feature_index is not None:
            return record.feature_index, True
        from .features import FeatureIndex

        record.feature_index = FeatureIndex.build(record.raw_features)
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
