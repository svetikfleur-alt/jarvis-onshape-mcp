"""Serializable state and response models for the governance pilot."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional


MAX_FOCUS_FEATURES = 12
MAX_HYPOTHESES = 8
MAX_EVIDENCE_REFS = 20
MAX_STATE_ID_LENGTH = 128
MAX_HYPOTHESIS_CLAIM_LENGTH = 500
MAX_PHASE_LENGTH = 32
_HYPOTHESIS_STATUSES = {"open", "supported", "rejected", "resolved"}


class WorkingStateValidationError(ValueError):
    """A client-supplied operational-state projection is invalid or unbounded."""


@dataclass(frozen=True)
class BudgetPolicy:
    """MVP limits for one model context cycle."""

    max_calls_per_cycle: int = 10
    max_onshape_reads_per_cycle: int = 6
    max_exploratory_reads_per_cycle: int = 6
    max_consecutive_reads: int = 4
    max_inline_bytes: int = 32768
    max_feature_rows: int = 50


@dataclass
class BudgetState:
    """Usage accumulated by one model context."""

    calls_used: int = 0
    onshape_reads_used: int = 0
    exploratory_reads_used: int = 0
    consecutive_reads: int = 0
    inline_bytes_returned: int = 0

    def record_call(
        self, *, onshape_read: bool = False, exploratory_read: bool = False
    ) -> None:
        self.calls_used += 1
        if onshape_read:
            self.onshape_reads_used += 1
        if exploratory_read:
            self.exploratory_reads_used += 1
            self.consecutive_reads += 1

    def reset_consecutive_reads(self) -> None:
        self.consecutive_reads = 0

    def record_inline_bytes(self, count: int) -> None:
        self.inline_bytes_returned += count

    def used(self) -> dict[str, int]:
        return {
            "calls": self.calls_used,
            "onshape_reads": self.onshape_reads_used,
            "exploratory_reads": self.exploratory_reads_used,
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
            "exploratory_reads": max(
                0,
                policy.max_exploratory_reads_per_cycle - self.exploratory_reads_used,
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
class Hypothesis:
    hypothesis_id: str
    claim: str
    status: str = "open"
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NextAction:
    capability: str
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_string(value: Any, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkingStateValidationError(f"{field_name} must be a non-empty string")
    if len(value) > max_length:
        raise WorkingStateValidationError(
            f"{field_name} exceeds the {max_length}-character limit"
        )
    return value


def _bounded_string_list(
    value: Any,
    *,
    field_name: str,
    max_items: int,
    max_length: int = MAX_STATE_ID_LENGTH,
) -> list[str]:
    if not isinstance(value, list):
        raise WorkingStateValidationError(f"{field_name} must be a list")
    if len(value) > max_items:
        raise WorkingStateValidationError(
            f"{field_name} exceeds the {max_items}-item limit"
        )
    result = [
        _bounded_string(item, field_name=field_name, max_length=max_length) for item in value
    ]
    if len(set(result)) != len(result):
        raise WorkingStateValidationError(f"{field_name} must not contain duplicates")
    return result


@dataclass
class WorkingState:
    context_handle: str
    model_ref: ModelRef
    revision_id: Optional[str] = None
    phase: str = "onboard"
    focus: list[str] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    next_action: Optional[NextAction] = None
    budget: BudgetState = field(default_factory=BudgetState)

    @property
    def focus_feature_ids(self) -> list[str]:
        return self.focus

    def has_narrowing_state(self) -> bool:
        has_open_hypothesis = any(item.status == "open" for item in self.hypotheses)
        has_concrete_action = bool(self.next_action and self.next_action.targets)
        return bool(self.focus) and (has_open_hypothesis or has_concrete_action)

    def projection(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "focus_feature_ids": list(self.focus),
            "hypotheses": [hypothesis.to_dict() for hypothesis in self.hypotheses],
            "evidence_refs": list(self.evidence_refs),
            "next_action": self.next_action.to_dict() if self.next_action else None,
        }

    def apply_operational_update(self, updates: dict[str, Any]) -> bool:
        """Atomically replace only validated, bounded operational fields."""

        focus = list(self.focus)
        hypotheses = list(self.hypotheses)
        evidence_refs = list(self.evidence_refs)
        next_action = self.next_action
        phase = self.phase

        if "focusFeatureIds" in updates:
            focus = _bounded_string_list(
                updates["focusFeatureIds"],
                field_name="focusFeatureIds",
                max_items=MAX_FOCUS_FEATURES,
            )

        if "hypotheses" in updates:
            raw_hypotheses = updates["hypotheses"]
            if not isinstance(raw_hypotheses, list):
                raise WorkingStateValidationError("hypotheses must be a list")
            if len(raw_hypotheses) > MAX_HYPOTHESES:
                raise WorkingStateValidationError(
                    f"hypotheses exceeds the {MAX_HYPOTHESES}-item limit"
                )
            hypotheses = []
            seen_ids: set[str] = set()
            for raw in raw_hypotheses:
                if not isinstance(raw, dict):
                    raise WorkingStateValidationError("each hypothesis must be an object")
                allowed = {"hypothesisId", "claim", "status", "evidenceRefs"}
                if set(raw) - allowed:
                    raise WorkingStateValidationError("hypothesis contains unsupported fields")
                hypothesis_id = _bounded_string(
                    raw.get("hypothesisId"),
                    field_name="hypothesisId",
                    max_length=64,
                )
                if hypothesis_id in seen_ids:
                    raise WorkingStateValidationError("hypothesisId values must be unique")
                seen_ids.add(hypothesis_id)
                claim = _bounded_string(
                    raw.get("claim"),
                    field_name="claim",
                    max_length=MAX_HYPOTHESIS_CLAIM_LENGTH,
                )
                status = raw.get("status", "open")
                if status not in _HYPOTHESIS_STATUSES:
                    raise WorkingStateValidationError(
                        "hypothesis status must be open, supported, rejected, or resolved"
                    )
                hypothesis_evidence = _bounded_string_list(
                    raw.get("evidenceRefs", []),
                    field_name="hypothesis evidenceRefs",
                    max_items=MAX_EVIDENCE_REFS,
                )
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=hypothesis_id,
                        claim=claim,
                        status=status,
                        evidence_refs=hypothesis_evidence,
                    )
                )

        if "evidenceRefs" in updates:
            evidence_refs = _bounded_string_list(
                updates["evidenceRefs"],
                field_name="evidenceRefs",
                max_items=MAX_EVIDENCE_REFS,
            )

        if "nextAction" in updates:
            raw_next_action = updates["nextAction"]
            if raw_next_action is None:
                next_action = None
            elif not isinstance(raw_next_action, dict):
                raise WorkingStateValidationError("nextAction must be an object or null")
            else:
                if set(raw_next_action) - {"capability", "targets"}:
                    raise WorkingStateValidationError("nextAction contains unsupported fields")
                capability = _bounded_string(
                    raw_next_action.get("capability"),
                    field_name="nextAction capability",
                    max_length=MAX_STATE_ID_LENGTH,
                )
                targets = _bounded_string_list(
                    raw_next_action.get("targets", []),
                    field_name="nextAction targets",
                    max_items=MAX_FOCUS_FEATURES,
                )
                next_action = NextAction(capability=capability, targets=targets)

        if "phase" in updates:
            proposed_phase = _bounded_string(
                updates["phase"], field_name="phase", max_length=MAX_PHASE_LENGTH
            )
            if self.phase == "complete" and proposed_phase != "complete":
                raise WorkingStateValidationError("complete is a terminal phase")
            if self.phase != "onboard" and proposed_phase == "onboard":
                raise WorkingStateValidationError("phase cannot transition back to onboard")
            phase = proposed_phase

        before = self.projection()
        self.focus = focus
        self.hypotheses = hypotheses
        self.evidence_refs = evidence_refs
        self.next_action = next_action
        self.phase = phase
        changed = self.projection() != before
        meaningful = changed and self.has_narrowing_state()
        if meaningful:
            self.budget.reset_consecutive_reads()
        return meaningful


@dataclass
class ObservationEnvelope:
    """Bounded response contract returned to an MCP client."""

    context_handle: str
    level: Literal["L0", "L1", "L2", "L3"]
    summary: Any
    data: dict[str, Any]
    truncated: bool = False
    cost: dict[str, int] = field(default_factory=lambda: {"inline_bytes": 0})
    budget_remaining: dict[str, int] = field(default_factory=dict)
    cache: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None

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
        if self.error is not None:
            result["error"] = self.error
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

    shrink_paths = (
        ("data", "edges"),
        ("data", "nodes"),
        ("data", "upstream"),
        ("data", "downstream"),
        ("data", "unresolved_references"),
        ("data", "feature", "parameters"),
        ("data", "rows"),
    )
    while size > policy.max_inline_bytes:
        candidates = [
            (path, _path_value(payload, path))
            for path in shrink_paths
            if isinstance(_path_value(payload, path), list) and _path_value(payload, path)
        ]
        if not candidates:
            break
        path, rows = max(candidates, key=lambda item: len(item[1]))
        rows.pop()
        payload["truncated"] = True
        if path == ("data", "rows"):
            payload["data"]["returned"] = len(rows)
            payload["data"]["has_more"] = True
        elif path == ("data", "feature", "parameters"):
            payload["data"]["feature"]["returned_parameters"] = len(rows)
            payload["data"]["feature"]["truncated"] = True
        elif path[:2] == ("data", "edges") or path[:2] == ("data", "nodes"):
            payload["data"]["truncated"] = True
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


def _path_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current
