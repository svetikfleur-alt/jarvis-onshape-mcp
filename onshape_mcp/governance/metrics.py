"""Small in-memory counters for transport and agent execution overhead."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from threading import Lock
from typing import Any, Mapping, Optional


def _tool_family(tool_name: str) -> str:
    if tool_name in {"get_runtime_info", "get_execution_metrics"}:
        return "runtime"
    if tool_name in {
        "list_entities",
        "get_body_details",
        "describe_part_studio",
        "inspect_sketch",
        "list_sketches",
    }:
        return "topology"
    if tool_name in {
        "get_features",
        "start_model_context",
        "get_feature_tree_compact",
        "find_features",
        "inspect_feature",
        "inspect_feature_dependencies",
        "get_dependency_slice",
        "get_context_status",
    }:
        return "model_state"
    if tool_name.startswith(("render_", "crop_", "list_cached_images")):
        return "rendering"
    if tool_name.startswith(("create_", "update_", "delete_", "edit_", "move_", "set_", "add_", "write_")):
        return "mutation"
    if "document" in tool_name or tool_name in {"get_elements", "find_part_studios"}:
        return "document"
    if "assembly" in tool_name or "instance" in tool_name or "mate" in tool_name:
        return "assembly"
    return "other"


class ExecutionMetrics:
    """Bounded process-local metrics with no request values or model payloads."""

    def __init__(self, *, max_recent_events: int = 64) -> None:
        self._lock = Lock()
        self._tool_invocations = 0
        self._tool_failures = 0
        self._response_bytes_total = 0
        self._response_families: dict[str, dict[str, int]] = {}
        self._recent_tool_events: deque[dict[str, Any]] = deque(
            maxlen=max_recent_events
        )
        self._http = {
            "calls": 0,
            "completed_calls": 0,
            "failed_calls": 0,
            "mutation_calls": 0,
            "broad_reads": 0,
            "targeted_reads": 0,
            "feature_list_refreshes": 0,
            "topology_reads": 0,
            "request_bytes": 0,
            "response_bytes": 0,
        }
        self._cache = {"hits": 0, "misses": 0, "invalidations": 0}
        self._semantic_verification_reads = 0

    def record_tool(
        self,
        tool_name: str,
        *,
        response_bytes: int,
        elapsed_ms: float,
        failed: bool = False,
    ) -> None:
        family = _tool_family(tool_name)
        with self._lock:
            self._tool_invocations += 1
            self._tool_failures += bool(failed)
            self._response_bytes_total += max(0, int(response_bytes))
            aggregate = self._response_families.setdefault(
                family, {"count": 0, "total_bytes": 0, "max_bytes": 0}
            )
            aggregate["count"] += 1
            aggregate["total_bytes"] += max(0, int(response_bytes))
            aggregate["max_bytes"] = max(
                aggregate["max_bytes"], max(0, int(response_bytes))
            )
            self._recent_tool_events.append(
                {
                    "tool": tool_name[:96],
                    "family": family,
                    "response_bytes": max(0, int(response_bytes)),
                    "elapsed_ms": round(max(0.0, float(elapsed_ms)), 3),
                    "failed": bool(failed),
                }
            )

    def record_http_attempt(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        request_bytes: int = 0,
    ) -> None:
        normalized_method = method.upper()
        feature_list = path.rstrip("/").endswith("/features")
        targeted = bool(feature_list and params and params.get("featureId"))
        with self._lock:
            self._http["calls"] += 1
            self._http["request_bytes"] += max(0, int(request_bytes))
            read_only_post = normalized_method == "POST" and path.rstrip("/").endswith(
                ("/featurescript", "/idtranslations")
            )
            if normalized_method in {"PUT", "PATCH", "DELETE"} or (
                normalized_method == "POST" and not read_only_post
            ):
                self._http["mutation_calls"] += 1
            if normalized_method == "GET" and feature_list:
                if targeted:
                    self._http["targeted_reads"] += 1
                else:
                    self._http["broad_reads"] += 1
                    self._http["feature_list_refreshes"] += 1
            if normalized_method == "GET" and "/bodydetails" in path:
                self._http["topology_reads"] += 1

    def record_http_completion(self, *, response_bytes: int) -> None:
        with self._lock:
            self._http["completed_calls"] += 1
            self._http["response_bytes"] += max(0, int(response_bytes))

    def record_http_failure(self) -> None:
        with self._lock:
            self._http["failed_calls"] += 1

    def record_cache(self, outcome: str) -> None:
        key = {
            "hit": "hits",
            "miss": "misses",
            "invalidation": "invalidations",
        }.get(outcome)
        if key is None:
            raise ValueError("cache outcome must be hit, miss, or invalidation")
        with self._lock:
            self._cache[key] += 1

    def record_semantic_verification_read(self) -> None:
        with self._lock:
            self._semantic_verification_reads += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tool_invocations": self._tool_invocations,
                "tool_failures": self._tool_failures,
                "approx_model_tool_round_trips": self._tool_invocations,
                "response_payload_bytes_total": self._response_bytes_total,
                "response_payloads_by_family": dict(
                    sorted(deepcopy(self._response_families).items())
                ),
                "http": dict(self._http),
                "cache": dict(self._cache),
                "semantic_verification_reads": self._semantic_verification_reads,
                "recent_tool_events": list(self._recent_tool_events),
            }
