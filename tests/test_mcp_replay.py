"""Deterministic full-route MCP replay contracts for Deep Read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import onshape_mcp.server as server
from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.partstudio import PartStudioManager
from onshape_mcp.api.request_guard import (
    BudgetedAsyncTransport,
    LiveBudgetGuard,
    LiveSuiteBudget,
)
from onshape_mcp.governance import ContextStore


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "partstudio"
FIXTURE_NAMES = ("small_clean", "unresolved_geometry_refs")


def partstudio_fixture(name: str) -> dict:
    """Load one public synthetic Part Studio fixture and its provenance."""
    if name not in FIXTURE_NAMES:
        raise ValueError(f"Unknown public Part Studio fixture: {name}")
    fixture_dir = FIXTURE_ROOT / name
    return {
        "metadata": json.loads(
            (fixture_dir / "metadata.json").read_text(encoding="utf-8")
        ),
        "features": json.loads(
            (fixture_dir / "features.json").read_text(encoding="utf-8")
        ),
    }


def _decode(result: list[Any]) -> dict[str, Any]:
    assert len(result) == 1
    return json.loads(result[0].text)


def _all_poison_canaries() -> set[str]:
    poisons: set[str] = set()
    for name in FIXTURE_NAMES:
        document = partstudio_fixture(name)["features"]
        for feature in document["features"]:
            for parameter in feature["parameters"]:
                unsupported = parameter.get("unsupportedRaw")
                if unsupported:
                    poisons.add(unsupported["poisonCanary"])
    return poisons


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


async def _run_full_replay(
    monkeypatch: pytest.MonkeyPatch, *, revision_present: bool
) -> dict[str, Any]:
    raw_features = json.loads(
        json.dumps(partstudio_fixture("small_clean")["features"])
    )
    if not revision_present:
        raw_features.pop("sourceMicroversion")

    request_paths: list[str] = []

    async def replay_response(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        assert request.method == "GET"
        assert request.url.path == "/api/v9/partstudios/d/doc/w/ws/e/el/features"
        return httpx.Response(200, json=raw_features)

    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    transport = BudgetedAsyncTransport(httpx.MockTransport(replay_response), guard)
    client = OnshapeClient(
        OnshapeCredentials(
            access_key="synthetic-access",
            secret_key="synthetic-secret",
            base_url="https://fixture.invalid",
        ),
        transport=transport,
    )
    monkeypatch.setattr(server, "partstudio_manager", PartStudioManager(client))
    monkeypatch.setattr(server, "context_store", ContextStore())

    envelopes: list[dict[str, Any]] = []
    try:
        started = _decode(
            await server.call_tool(
                "start_model_context",
                {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
            )
        )
        envelopes.append(started)
        handle = started["context_handle"]

        tree = _decode(
            await server.call_tool(
                "get_feature_tree_compact",
                {"contextHandle": handle, "offset": 0, "limit": 500},
            )
        )
        envelopes.append(tree)
        found = _decode(
            await server.call_tool(
                "find_features",
                {"contextHandle": handle, "query": "main", "limit": 500},
            )
        )
        envelopes.append(found)
        first_inspection = _decode(
            await server.call_tool(
                "inspect_feature",
                {"contextHandle": handle, "featureId": "extrude-1"},
            )
        )
        envelopes.append(first_inspection)
        dependency_slice = _decode(
            await server.call_tool(
                "get_dependency_slice",
                {
                    "contextHandle": handle,
                    "featureId": "extrude-1",
                    "direction": "both",
                    "depth": 20,
                    "maxNodes": 500,
                    "maxEdges": 500,
                },
            )
        )
        envelopes.append(dependency_slice)
        updated = _decode(
            await server.call_tool(
                "update_working_state",
                {
                    "contextHandle": handle,
                    "focusFeatureIds": ["extrude-1"],
                    "hypotheses": [
                        {
                            "hypothesisId": "h-extrude-input",
                            "claim": "Main Extrude consumes Base Profile",
                            "status": "open",
                            "evidenceRefs": ["fixture:sketch-1"],
                        }
                    ],
                },
            )
        )
        envelopes.append(updated)
        repeated_inspection = _decode(
            await server.call_tool(
                "inspect_feature",
                {"contextHandle": handle, "featureId": "extrude-1"},
            )
        )
        envelopes.append(repeated_inspection)
        status = _decode(
            await server.call_tool("get_context_status", {"contextHandle": handle})
        )
        envelopes.append(status)
        stored_revision = server.context_store.get(handle).working_state.revision_id
    finally:
        await client.close()

    return {
        "request_paths": request_paths,
        "guard_used": guard.used,
        "suite_used": suite.used,
        "started": started,
        "tree": tree,
        "found": found,
        "first_inspection": first_inspection,
        "dependency_slice": dependency_slice,
        "updated": updated,
        "repeated_inspection": repeated_inspection,
        "status": status,
        "stored_revision": stored_revision,
        "envelopes": envelopes,
    }


async def _run_unresolved_gate_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    raw_features = partstudio_fixture("unresolved_geometry_refs")["features"]
    request_paths: list[str] = []

    async def replay_response(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        assert request.method == "GET"
        assert request.url.path == "/api/v9/partstudios/d/doc/w/ws/e/el/features"
        return httpx.Response(200, json=raw_features)

    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    transport = BudgetedAsyncTransport(httpx.MockTransport(replay_response), guard)
    client = OnshapeClient(
        OnshapeCredentials(
            access_key="synthetic-access",
            secret_key="synthetic-secret",
            base_url="https://fixture.invalid",
        ),
        transport=transport,
    )
    store = ContextStore()
    monkeypatch.setattr(server, "partstudio_manager", PartStudioManager(client))
    monkeypatch.setattr(server, "context_store", store)

    envelopes: list[dict[str, Any]] = []
    try:
        started = _decode(
            await server.call_tool(
                "start_model_context",
                {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
            )
        )
        envelopes.append(started)
        handle = started["context_handle"]
        for tool_name, arguments in (
            ("get_feature_tree_compact", {"offset": 0, "limit": 500}),
            ("find_features", {"query": "main", "limit": 500}),
            ("inspect_feature", {"featureId": "extrude-1"}),
            (
                "get_dependency_slice",
                {
                    "featureId": "extrude-1",
                    "direction": "both",
                    "depth": 20,
                    "maxNodes": 500,
                    "maxEdges": 500,
                },
            ),
        ):
            envelope = _decode(
                await server.call_tool(
                    tool_name, {"contextHandle": handle, **arguments}
                )
            )
            envelopes.append(envelope)

        gated = _decode(
            await server.call_tool(
                "inspect_feature_dependencies",
                {"contextHandle": handle, "featureId": "fillet-1"},
            )
        )
        envelopes.append(gated)
        requests_at_gate = len(request_paths)
        cache_reads_at_gate = store.get(handle).ledger.cache_reads

        updated = _decode(
            await server.call_tool(
                "update_working_state",
                {
                    "contextHandle": handle,
                    "focusFeatureIds": ["fillet-1"],
                    "hypotheses": [
                        {
                            "hypothesisId": "h-unresolved-edge",
                            "claim": "Check whether the fillet edge has a proven producer",
                            "status": "open",
                        }
                    ],
                },
            )
        )
        envelopes.append(updated)
        unresolved = _decode(
            await server.call_tool(
                "inspect_feature_dependencies",
                {"contextHandle": handle, "featureId": "fillet-1"},
            )
        )
        envelopes.append(unresolved)
        status = _decode(
            await server.call_tool("get_context_status", {"contextHandle": handle})
        )
        envelopes.append(status)
    finally:
        await client.close()

    return {
        "gated": gated,
        "requests_at_gate": requests_at_gate,
        "cache_reads_at_gate": cache_reads_at_gate,
        "unresolved": unresolved,
        "request_count": len(request_paths),
        "guard_used": guard.used,
        "suite_used": suite.used,
        "status": status,
        "envelopes": envelopes,
    }


async def _run_stress_replay(
    monkeypatch: pytest.MonkeyPatch, *, feature_count: int, name_size: int
) -> dict[str, Any]:
    raw_features = {
        "serializationVersion": "1.2.3",
        "sourceMicroversion": "synthetic-stress-microversion",
        "features": [
            {
                "btType": "BTMSketch-151",
                "featureId": f"feature-{index:03d}",
                "featureType": "newSketch",
                "name": f"Feature {index:03d} " + ("x" * name_size),
                "suppressed": False,
                "parameters": [
                    {
                        "btType": "BTMParameterUnknown-999",
                        "parameterId": "private-extension",
                        "unsupportedRaw": {
                            "poisonCanary": f"RAW_POISON_STRESS_PRIVATE_{index:03d}"
                        },
                    }
                ],
            }
            for index in range(feature_count)
        ],
        "featureStates": {
            f"feature-{index:03d}": {"featureStatus": "OK", "messages": []}
            for index in range(feature_count)
        },
    }
    request_paths: list[str] = []

    async def replay_response(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        assert request.method == "GET"
        assert request.url.path == "/api/v9/partstudios/d/doc/w/ws/e/el/features"
        return httpx.Response(200, json=raw_features)

    suite = LiveSuiteBudget(limit=1)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    transport = BudgetedAsyncTransport(httpx.MockTransport(replay_response), guard)
    client = OnshapeClient(
        OnshapeCredentials(
            access_key="synthetic-access",
            secret_key="synthetic-secret",
            base_url="https://fixture.invalid",
        ),
        transport=transport,
    )
    monkeypatch.setattr(server, "partstudio_manager", PartStudioManager(client))
    monkeypatch.setattr(server, "context_store", ContextStore())

    try:
        started = _decode(
            await server.call_tool(
                "start_model_context",
                {"documentId": "doc", "workspaceId": "ws", "elementId": "el"},
            )
        )
        handle = started["context_handle"]
        first_page = _decode(
            await server.call_tool(
                "get_feature_tree_compact",
                {"contextHandle": handle, "offset": 0, "limit": 500},
            )
        )
        continuation = _decode(
            await server.call_tool(
                "get_feature_tree_compact",
                {"contextHandle": handle, "offset": 50, "limit": 500},
            )
        )
    finally:
        await client.close()

    return {
        "first_page": first_page,
        "continuation": continuation,
        "request_count": len(request_paths),
        "guard_used": guard.used,
        "suite_used": suite.used,
    }


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_public_partstudio_fixtures_have_provenance_and_private_poison(name: str) -> None:
    fixture = partstudio_fixture(name)
    metadata = fixture["metadata"]
    features = fixture["features"]

    assert metadata == {
        "source": "hand-authored synthetic Onshape-shaped data",
        "purpose": metadata["purpose"],
        "feature_count": len(features["features"]),
        "privacy_status": "synthetic-public",
    }
    assert metadata["purpose"]
    poison_values = {
        parameter["unsupportedRaw"]["poisonCanary"]
        for feature in features["features"]
        for parameter in feature["parameters"]
        if "unsupportedRaw" in parameter
    }
    assert len(poison_values) == len(features["features"])
    assert all(value.startswith("RAW_POISON_") for value in poison_values)


@pytest.mark.asyncio
@pytest.mark.parametrize("revision_present", [True, False])
async def test_full_public_mcp_route_reuses_one_guarded_physical_send(
    monkeypatch: pytest.MonkeyPatch, revision_present: bool
) -> None:
    replay = await _run_full_replay(monkeypatch, revision_present=revision_present)

    assert replay["request_paths"] == [
        "/api/v9/partstudios/d/doc/w/ws/e/el/features"
    ]
    assert replay["guard_used"] == 1
    assert replay["suite_used"] == 1
    assert replay["status"]["data"]["ledger"] == {
        "onshape_reads": 1,
        "cache_reads": 5,
    }
    assert replay["first_inspection"]["cache"]["cache_repeat"] is False
    assert replay["repeated_inspection"]["cache"]["cache_repeat"] is True
    assert replay["tree"]["data"]["returned"] == 2
    assert replay["tree"]["data"]["has_more"] is False
    assert replay["found"]["data"]["rows"][0]["featureId"] == "extrude-1"
    assert replay["first_inspection"]["data"]["feature"]["status"] == "OK"
    assert replay["dependency_slice"]["data"]["limits"] == {
        "depth": 3,
        "maxNodes": 80,
        "maxEdges": 160,
    }
    assert replay["dependency_slice"]["data"]["edges"] == [
        {
            "evidenceType": "EXPLICIT_QUERY_REFERENCE",
            "fromFeatureId": "extrude-1",
            "toFeatureId": "sketch-1",
        }
    ]

    expected_revision: str | None = (
        "synthetic-microversion-001" if revision_present else None
    )
    assert replay["started"]["summary"]["revision_id"] == expected_revision
    assert replay["stored_revision"] == expected_revision
    assert replay["status"]["data"]["revision_id"] == expected_revision

    serialized = json.dumps(replay["envelopes"], sort_keys=True)
    for poison in _all_poison_canaries():
        assert poison not in serialized
    assert not _contains_key(replay["envelopes"], "features")
    assert all(
        envelope["cost"]["inline_bytes"] <= 32768
        for envelope in replay["envelopes"]
    )


@pytest.mark.asyncio
async def test_gate_precedes_cached_access_and_unresolved_geometry_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = await _run_unresolved_gate_replay(monkeypatch)

    assert replay["gated"]["error"]["type"] == "HYPOTHESIS_REQUIRED"
    assert replay["requests_at_gate"] == 1
    assert replay["cache_reads_at_gate"] == 4
    assert replay["unresolved"]["data"]["upstream"] == []
    assert replay["unresolved"]["data"]["downstream"] == []
    assert replay["unresolved"]["data"]["unresolved_references"] == [
        {
            "parameterId": "entities",
            "evidenceType": "UNRESOLVED_GEOMETRY_REFERENCE",
            "referenceType": "BTMIndividualQuery-138",
            "reason": "Geometry reference has no proven producing feature",
            "identifierCount": 1,
        }
    ]
    assert replay["request_count"] == 1
    assert replay["guard_used"] == 1
    assert replay["suite_used"] == 1
    assert replay["status"]["data"]["ledger"] == {
        "onshape_reads": 1,
        "cache_reads": 5,
    }
    serialized = json.dumps(replay["envelopes"], sort_keys=True)
    for poison in _all_poison_canaries():
        assert poison not in serialized
    assert not _contains_key(replay["envelopes"], "features")
    assert all(
        envelope["cost"]["inline_bytes"] <= 32768
        for envelope in replay["envelopes"]
    )


@pytest.mark.asyncio
async def test_public_mcp_compact_tree_enforces_page_cap_and_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = await _run_stress_replay(monkeypatch, feature_count=75, name_size=0)

    first = replay["first_page"]
    continuation = replay["continuation"]
    assert first["data"]["total"] == 75
    assert first["data"]["offset"] == 0
    assert first["data"]["returned"] == 50
    assert len(first["data"]["rows"]) == 50
    assert first["data"]["has_more"] is True
    assert first["data"]["rows"][0]["ordinal"] == 1
    assert first["data"]["rows"][-1]["ordinal"] == 50
    assert continuation["data"]["total"] == 75
    assert continuation["data"]["offset"] == 50
    assert continuation["data"]["returned"] == 25
    assert len(continuation["data"]["rows"]) == 25
    assert continuation["data"]["has_more"] is False
    assert continuation["data"]["rows"][0]["ordinal"] == 51
    assert continuation["data"]["rows"][-1]["ordinal"] == 75
    assert len(json.dumps(first).encode("utf-8")) <= 32768
    assert len(json.dumps(continuation).encode("utf-8")) <= 32768
    assert replay["request_count"] == 1
    assert replay["guard_used"] == 1
    assert replay["suite_used"] == 1


@pytest.mark.asyncio
async def test_public_mcp_compact_tree_bounds_oversized_normalized_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = await _run_stress_replay(monkeypatch, feature_count=50, name_size=2000)

    page = replay["first_page"]
    encoded = json.dumps(page, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert page["truncated"] is True
    assert page["data"]["returned"] < 50
    assert page["data"]["returned"] == len(page["data"]["rows"])
    assert page["data"]["has_more"] is True
    assert len(encoded.encode("utf-8")) <= 32768
    assert page["cost"]["inline_bytes"] == len(encoded.encode("utf-8"))
    assert "RAW_POISON_STRESS_PRIVATE" not in encoded
    assert "unsupportedRaw" not in encoded
    assert not _contains_key(page, "features")
    assert replay["request_count"] == 1
    assert replay["guard_used"] == 1
    assert replay["suite_used"] == 1
