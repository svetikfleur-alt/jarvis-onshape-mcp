"""Bounded measurement of HTTP and model/tool execution overhead."""

import json

import httpx
import pytest

import onshape_mcp.server as server
from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.governance.metrics import ExecutionMetrics


@pytest.mark.parametrize(
    ("tool_name", "expected_family"),
    [
        ("execute_feature", "mutation"),
        ("inspect_feature_compact", "model_state"),
        ("get_compact_model_state", "model_state"),
        ("get_execution_protocol_metrics", "runtime"),
    ],
)
def test_perf_metrics_classify_execution_protocol_tools(
    tool_name: str, expected_family: str
) -> None:
    metrics = ExecutionMetrics()

    metrics.record_tool(tool_name, response_bytes=1, elapsed_ms=1.0)

    assert metrics.snapshot()["response_payloads_by_family"] == {
        expected_family: {"count": 1, "total_bytes": 1, "max_bytes": 1}
    }


@pytest.mark.asyncio
async def test_http_metrics_distinguish_targeted_broad_topology_and_mutation_reads() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    metrics = ExecutionMetrics()
    credentials = OnshapeCredentials(access_key="test", secret_key="test")
    async with OnshapeClient(
        credentials,
        transport=httpx.MockTransport(handler),
        metrics=metrics,
    ) as client:
        base = "/api/v9/partstudios/d/doc-private/w/ws-private/e/el-private"
        await client.get(f"{base}/features", params={"featureId": ["feature-private"]})
        await client.get(f"{base}/features")
        await client.get(f"{base}/bodydetails")
        await client.post(f"{base}/features", data={"feature": {"name": "private"}})
        await client.post(f"{base}/featurescript", data={"script": "private"})

    snapshot = metrics.snapshot()
    assert snapshot["http"] == {
        "calls": 5,
        "completed_calls": 5,
        "failed_calls": 0,
        "mutation_calls": 1,
        "broad_reads": 1,
        "targeted_reads": 1,
        "feature_list_refreshes": 1,
        "topology_reads": 1,
        "request_bytes": 50,
        "response_bytes": 55,
    }
    assert "private" not in json.dumps(snapshot)


@pytest.mark.asyncio
async def test_mcp_metrics_count_invocations_round_trips_and_response_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = ExecutionMetrics()
    monkeypatch.setattr(server, "execution_metrics", metrics)

    runtime_result = await server.call_tool("get_runtime_info", {})
    runtime_size = len(runtime_result[0].text.encode("utf-8"))
    metrics_result = await server.call_tool("get_execution_metrics", {})
    reported = json.loads(metrics_result[0].text)

    assert reported["tool_invocations"] == 1
    assert reported["approx_model_tool_round_trips"] == 1
    assert reported["response_payload_bytes_total"] == runtime_size
    assert reported["response_payloads_by_family"]["runtime"]["count"] == 1
    final = metrics.snapshot()
    assert final["tool_invocations"] == 2
    assert final["recent_tool_events"][-1]["tool"] == "get_execution_metrics"
    assert len(final["recent_tool_events"]) == 2


@pytest.mark.asyncio
async def test_http_attempt_is_counted_when_transport_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    metrics = ExecutionMetrics()
    credentials = OnshapeCredentials(access_key="test", secret_key="test")
    async with OnshapeClient(
        credentials,
        transport=httpx.MockTransport(handler),
        metrics=metrics,
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.get("/api/v9/partstudios/d/private/w/private/e/private/features")

    snapshot = metrics.snapshot()
    assert snapshot["http"]["calls"] == 1
    assert snapshot["http"]["completed_calls"] == 0
    assert snapshot["http"]["failed_calls"] == 1


@pytest.mark.asyncio
async def test_failed_mcp_invocation_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = ExecutionMetrics()
    monkeypatch.setattr(server, "execution_metrics", metrics)

    with pytest.raises(ValueError, match="Unknown tool"):
        await server.call_tool("unknown_tool", {})

    snapshot = metrics.snapshot()
    assert snapshot["tool_invocations"] == 1
    assert snapshot["tool_failures"] == 1
    assert snapshot["recent_tool_events"][-1]["failed"] is True
