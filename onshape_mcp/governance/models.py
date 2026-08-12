"""Serializable state and response models for the governance pilot."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class BudgetPolicy:
    """MVP limits for one model context cycle."""

    max_calls_per_cycle: int = 10
    max_onshape_reads_per_cycle: int = 6
    max_consecutive_reads: int = 4
    max_inline_bytes: int = 32768
    max_feature_rows: int = 50


@dataclass
class BudgetState:
    """Usage accumulated by one model context."""

    calls_used: int = 0
    onshape_reads_used: int = 0
    consecutive_reads: int = 0
    inline_bytes_returned: int = 0

    def record_call(self, *, onshape_read: bool = False) -> None:
        self.calls_used += 1
        if onshape_read:
            self.onshape_reads_used += 1
            self.consecutive_reads += 1
        else:
            self.consecutive_reads = 0

    def record_inline_bytes(self, count: int) -> None:
        self.inline_bytes_returned += count

    def used(self) -> dict[str, int]:
        return {
            "calls": self.calls_used,
            "onshape_reads": self.onshape_reads_used,
            "consecutive_reads": self.consecutive_reads,
            "inline_bytes": self.inline_bytes_returned,
        }

    def remaining(
        self, policy: BudgetPolicy, *, reserved_inline_bytes: int = 0
    ) -> dict[str, int]:
        return {
            "calls": max(0, policy.max_calls_per_cycle - self.calls_used),
            "onshape_reads": max(
                0, policy.max_onshape_reads_per_cycle - self.onshape_reads_used
            ),
            "consecutive_reads": max(
                0, policy.max_consecutive_reads - self.consecutive_reads
            ),
            "inline_bytes": max(
                0,
                policy.max_inline_bytes
                - self.inline_bytes_returned
                - reserved_inline_bytes,
            ),
        }


@dataclass(frozen=True)
class ModelRef:
    document_id: str
    workspace_id: str
    element_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class WorkingState:
    context_handle: str
    model_ref: ModelRef
    revision_id: str | None = None
    phase: str = "onboard"
    focus: list[str] = field(default_factory=list)
    budget: BudgetState = field(default_factory=BudgetState)


@dataclass
class ObservationEnvelope:
    """Bounded response contract returned to an MCP client."""

    context_handle: str
    level: Literal["L0", "L1"]
    summary: Any
    data: dict[str, Any]
    truncated: bool = False
    cost: dict[str, int] = field(default_factory=lambda: {"inline_bytes": 0})
    budget_remaining: dict[str, int] = field(default_factory=dict)
    cache: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "context_handle": self.context_handle,
            "level": self.level,
            "summary": self.summary,
            "data": self.data,
            "truncated": self.truncated,
            "cost": self.cost,
            "budget_remaining": self.budget_remaining,
        }
        if self.cache is not None:
            result["cache"] = self.cache
        return result


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _finalize_cost(
    payload: dict[str, Any], policy: BudgetPolicy, budget: BudgetState
) -> tuple[str, int]:
    """Resolve the size fields until their own digit lengths are stable."""

    for _ in range(12):
        text = _encode(payload)
        size = len(text.encode("utf-8"))
        cost = {"inline_bytes": size}
        remaining = budget.remaining(policy, reserved_inline_bytes=size)
        if payload.get("cost") == cost and payload.get("budget_remaining") == remaining:
            return text, size
        payload["cost"] = cost
        payload["budget_remaining"] = remaining

    text = _encode(payload)
    return text, len(text.encode("utf-8"))


def serialize_bounded(
    envelope: ObservationEnvelope, policy: BudgetPolicy, budget: BudgetState
) -> str:
    """Serialize first, then shrink paged rows before crossing the byte limit."""

    payload = copy.deepcopy(envelope.to_dict())
    text, size = _finalize_cost(payload, policy, budget)

    rows = payload.get("data", {}).get("rows")
    while size > policy.max_inline_bytes and isinstance(rows, list) and rows:
        rows.pop()
        payload["data"]["returned"] = len(rows)
        payload["data"]["has_more"] = True
        payload["truncated"] = True
        text, size = _finalize_cost(payload, policy, budget)

    if size <= policy.max_inline_bytes:
        return text

    payload["summary"] = {"message": "Response reduced to fit inline byte limit"}
    payload["data"] = {}
    payload["truncated"] = True
    payload.pop("cache", None)
    text, size = _finalize_cost(payload, policy, budget)
    if size <= policy.max_inline_bytes:
        return text

    error = _encode(
        {
            "error": {
                "type": "inline_budget_exceeded",
                "message": "Response cannot fit inline byte limit",
            }
        }
    )
    if len(error.encode("utf-8")) <= policy.max_inline_bytes:
        return error
    return "{}"
