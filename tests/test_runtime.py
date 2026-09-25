"""Offline contracts for the canonical Jarvis MCP runtime."""

from __future__ import annotations

from importlib.metadata import version
import importlib
import json
from pathlib import Path

import pytest

import onshape_mcp
import onshape_mcp.runtime as runtime
import onshape_mcp.server as server


ROOT = Path(__file__).resolve().parents[1]


def test_package_version_comes_from_installed_project_metadata() -> None:
    assert onshape_mcp.__version__ == version("onshape-mcp")


def test_runtime_environment_loader_targets_only_repository_root_dotenv(monkeypatch) -> None:
    calls = []

    def fake_load_dotenv(path, *, override):
        calls.append((Path(path), override))
        return True

    monkeypatch.setattr(runtime, "load_dotenv", fake_load_dotenv)

    assert runtime.load_runtime_environment() is True
    assert calls == [(ROOT / ".env", False)]
    assert runtime.DOTENV_PATH == ROOT / ".env"


def test_runtime_info_reports_source_and_python_without_secret_values() -> None:
    environment = {
        "ONSHAPE_API_KEY": "private-access-canary",
        "ONSHAPE_API_SECRET": "private-secret-canary",
    }

    diagnostic = runtime.get_runtime_info(environment)

    assert diagnostic["server_status"] == "initialized"
    assert diagnostic["package"] == {
        "name": "onshape-mcp",
        "version": version("onshape-mcp"),
    }
    assert Path(diagnostic["runtime"]["server_module"]) == ROOT / "onshape_mcp" / "server.py"
    assert Path(diagnostic["runtime"]["project_root"]) == ROOT
    assert Path(diagnostic["runtime"]["dotenv_path"]) == ROOT / ".env"
    assert diagnostic["runtime"]["python_executable"]
    assert diagnostic["runtime"]["python_version"]
    assert diagnostic["credential_configuration"] == {
        "status": "present",
        "selected_alias": "ONSHAPE_API_KEY + ONSHAPE_API_SECRET",
        "warnings": [],
    }
    assert diagnostic["network_auth_status"] == "not_tested"
    assert diagnostic["live_authenticated_read_status"] == "not_tested"
    rendered = json.dumps(diagnostic, sort_keys=True)
    for secret in environment.values():
        assert secret not in rendered


@pytest.mark.asyncio
async def test_cli_and_mcp_diagnostics_call_the_same_runtime_info_implementation(
    monkeypatch, capsys
) -> None:
    expected = {"shared-runtime-info": True}
    calls = []

    def fake_runtime_info():
        calls.append("called")
        return expected

    def fail_if_stdio_starts(*args, **kwargs):
        raise AssertionError("stdio transport started during runtime diagnostic")

    monkeypatch.setattr(server, "get_runtime_info", fake_runtime_info, raising=False)
    monkeypatch.setattr(server.asyncio, "run", fail_if_stdio_starts)

    server.main(["--runtime-info"])
    cli_payload = json.loads(capsys.readouterr().out)
    mcp_result = await server.call_tool("get_runtime_info", {})
    mcp_payload = json.loads(mcp_result[0].text)

    assert cli_payload == expected
    assert mcp_payload == expected
    assert calls == ["called", "called"]


@pytest.mark.asyncio
async def test_runtime_info_is_exposed_as_a_closed_read_only_mcp_tool() -> None:
    tools = await server.list_tools()
    diagnostic = next(tool for tool in tools if tool.name == "get_runtime_info")

    assert diagnostic.inputSchema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "credential values" in diagnostic.description.lower()
    assert "network" in diagnostic.description.lower()


@pytest.mark.parametrize("module_name", ["tools.agent_loop", "tools.cad_driver"])
def test_auxiliary_direct_clients_use_canonical_complete_pair_resolution(
    module_name, monkeypatch
) -> None:
    for name in (
        "ONSHAPE_ACCESS_KEY",
        "ONSHAPE_SECRET_KEY",
        "ONSHAPE_API_KEY",
        "ONSHAPE_API_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "access-canary")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "secret-canary")
    monkeypatch.setenv("ONSHAPE_API_SECRET", "incomplete-alternate-canary")
    module = importlib.import_module(module_name)

    credentials = module._resolve_runtime_credentials()

    assert (credentials.access_key, credentials.secret_key) == (
        "access-canary",
        "secret-canary",
    )


@pytest.mark.parametrize(
    ("module_name", "leaf"),
    [
        ("tools.agent_loop", "agent-runs"),
        ("tools.agent_sdk_loop", "agent-sdk-runs"),
    ],
)
def test_auxiliary_harness_output_defaults_stay_inside_checkout(module_name, leaf) -> None:
    module = importlib.import_module(module_name)

    assert module.DEFAULT_OUTPUT_ROOT == ROOT / "scratchpad" / leaf
