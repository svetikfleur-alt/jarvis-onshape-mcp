"""In-memory context storage and feature normalization for the pilot."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .models import BudgetPolicy, ModelRef, WorkingState


class ContextNotFoundError(LookupError):
    def __init__(self, context_handle: str):
        super().__init__("Unknown context handle")
        self.context_handle = context_handle


@dataclass
class ReadLedger:
    onshape_reads: int = 0
    cache_reads: int = 0
    events: list[str] = field(default_factory=list)

    def record_onshape_read(self) -> None:
        self.onshape_reads += 1
        self.events.append("onshape:get_features")

    def record_cache_read(self) -> None:
        self.cache_reads += 1
        self.events.append("cache:get_features")

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


class ContextStore:
    """Small storage boundary whose implementation can later be replaced by SQLite."""

    def __init__(self, policy: BudgetPolicy | None = None):
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
        }
        return record

    def record_onshape_read(self, context_handle: str) -> None:
        self.get(context_handle).ledger.record_onshape_read()

    def record_cache_read(self, context_handle: str) -> None:
        self.get(context_handle).ledger.record_cache_read()


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


def extract_revision_id(raw_features: Any) -> str | None:
    """Use only Onshape's explicit source microversion, never a serialization version."""

    if not isinstance(raw_features, dict):
        return None
    value = raw_features.get("sourceMicroversion")
    return value if isinstance(value, str) and value else None


def summarize_features(raw_features: Any, revision_id: str | None) -> dict[str, Any]:
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
    status_filter: str | None = None,
    text_query: str | None = None,
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
