"""Session-local provenance for baseline-aware document hygiene."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ElementClass = Literal["intentional", "temporary"]


def _value(element: Any, *names: str) -> Any:
    for name in names:
        if isinstance(element, dict) and name in element:
            return element[name]
        if hasattr(element, name):
            return getattr(element, name)
    return None


def _project(element: Any) -> dict[str, str]:
    element_id = _value(element, "id")
    if not isinstance(element_id, str) or not element_id:
        raise ValueError("document element must have a non-empty id")
    name = _value(element, "name")
    element_type = _value(element, "element_type", "elementType", "type")
    return {
        "id": element_id,
        "name": name if isinstance(name, str) else "",
        "type": element_type if isinstance(element_type, str) else "UNKNOWN",
    }


def _ordered(values: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    return [values[key] for key in sorted(values)]


@dataclass
class _WorkspaceHygiene:
    baseline: dict[str, dict[str, str]] = field(default_factory=dict)
    intentional: dict[str, dict[str, str]] = field(default_factory=dict)
    temporary: dict[str, dict[str, str]] = field(default_factory=dict)


class DocumentHygieneTracker:
    """Track provenance without deleting or relabeling unknown elements."""

    def __init__(self) -> None:
        self._workspaces: dict[tuple[str, str], _WorkspaceHygiene] = {}

    def capture_baseline(
        self, document_id: str, workspace_id: str, elements: list[Any]
    ) -> None:
        self._workspaces[(document_id, workspace_id)] = _WorkspaceHygiene(
            baseline={item["id"]: item for item in map(_project, elements)}
        )

    def register_addition(
        self,
        document_id: str,
        workspace_id: str,
        element: Any,
        *,
        classification: ElementClass,
    ) -> None:
        state = self._workspaces.get((document_id, workspace_id))
        if state is None:
            return
        projected = _project(element)
        if classification == "intentional":
            state.intentional[projected["id"]] = projected
            state.temporary.pop(projected["id"], None)
        elif classification == "temporary":
            state.temporary[projected["id"]] = projected
            state.intentional.pop(projected["id"], None)
        else:
            raise ValueError("classification must be intentional or temporary")

    def evaluate(
        self, document_id: str, workspace_id: str, elements: list[Any]
    ) -> dict[str, Any]:
        state = self._workspaces.get((document_id, workspace_id))
        if state is None:
            return {
                "baseline_available": False,
                "status": "UNKNOWN",
                "reason": "No bootstrap baseline is available for this document workspace.",
            }

        current = {item["id"]: item for item in map(_project, elements)}
        intentional = {
            key: value for key, value in state.intentional.items() if key in current
        }
        temporary = {
            key: value for key, value in state.temporary.items() if key in current
        }
        known_ids = set(state.baseline) | set(intentional) | set(temporary)
        unknown = {key: value for key, value in current.items() if key not in known_ids}
        leftovers = dict(temporary)
        return {
            "baseline_available": True,
            "baseline_elements": _ordered(state.baseline),
            "intentional_additions": _ordered(intentional),
            "temporary_jarvis_additions": _ordered(temporary),
            "remaining_unexpected_jarvis_artifacts": _ordered(leftovers),
            "unknown_additions": _ordered(unknown),
            "status": "NOT CLEAN" if leftovers else "CLEAN",
        }
