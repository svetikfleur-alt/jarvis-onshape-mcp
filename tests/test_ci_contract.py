"""Repository-policy contracts for credential-free CI and local live opt-in."""

from __future__ import annotations

import re
import json
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPOSITORY = "https://github.com/svetikfleur-alt/jarvis-onshape-mcp"
CANONICAL_PLUGIN_ARGS = [
    "--directory",
    "${CLAUDE_PLUGIN_ROOT}",
    "run",
    "--frozen",
    "onshape-mcp",
]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _normalized(relative_path: str) -> str:
    return re.sub(r"\s+", " ", _read(relative_path)).strip()


def test_pytest_defaults_are_fast_and_pyproject_owns_coverage_policy() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    addopts = config["tool"]["pytest"]["ini_options"]["addopts"]

    assert addopts == ["-v", "-ra", "--strict-markers"]
    assert config["tool"]["coverage"]["run"]["branch"] is True
    assert config["tool"]["coverage"]["report"]["fail_under"] == 80
    assert not (ROOT / ".coveragerc").exists()


def test_plugin_defers_credentials_to_the_runtime_environment() -> None:
    plugin = json.loads(_read(".claude-plugin/plugin.json"))
    server = plugin["mcpServers"]["onshape"]
    project = tomllib.loads(_read("pyproject.toml"))["project"]

    assert "userConfig" not in plugin
    assert "env" not in server
    assert server["command"] == "uv"
    assert server["args"] == CANONICAL_PLUGIN_ARGS
    assert plugin["repository"] == CANONICAL_REPOSITORY
    assert plugin["version"] == project["version"]


def test_canonical_runtime_documentation_uses_portable_checkout_placeholder() -> None:
    readme = _read("README.md")

    assert "uv --directory <repo-root> run --frozen onshape-mcp" in readme
    assert (
        "codex mcp add jarvis-onshape -- uv --directory $jarvisRoot "
        "run --frozen onshape-mcp"
    ) in readme
    assert "<repo-root>" in readme
    assert f"github:{CANONICAL_REPOSITORY.removeprefix('https://github.com/')}" in readme
    assert f"git clone {CANONICAL_REPOSITORY}.git" in readme
    assert "github:ReshefElisha/jarvis-onshape-mcp" not in readme
    assert "git clone https://github.com/ReshefElisha/jarvis-onshape-mcp" not in readme
    assert "Codex CLI, IDE extension, ChatGPT Desktop, or Claude Code" in readme
    assert "restart Claude Code and try" not in readme


def test_env_example_contains_only_blank_supported_alias_assignments() -> None:
    lines = _read(".env.example").splitlines()

    assert lines == [
        "ONSHAPE_ACCESS_KEY=",
        "ONSHAPE_SECRET_KEY=",
        "ONSHAPE_API_KEY=",
        "ONSHAPE_API_SECRET=",
    ]


def test_generated_runtime_state_is_ignored_without_hiding_env_template() -> None:
    ignored = (
        ".env",
        ".codex/config.toml",
        "graphify-out/graph.json",
        ".pytest_cache/state",
        ".pytest-tmp/session",
        ".ruff_cache/state",
        ".mypy_cache/state",
    )
    for path in ignored:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, path

    template = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", ".env.example"],
        cwd=ROOT,
        check=False,
    )
    assert template.returncode == 1


def test_release_workflow_updates_package_and_plugin_metadata_only() -> None:
    workflow = _read(".github/workflows/release-prep.yml")

    assert "pyproject.toml" in workflow
    assert ".claude-plugin/plugin.json" in workflow
    assert "onshape_mcp/__init__.py" not in workflow


def test_pr_workflow_is_one_blocking_credential_free_python_312_job() -> None:
    workflow = _read(".github/workflows/test.yml")
    normalized = _normalized(".github/workflows/test.yml")
    jobs = workflow.split("\njobs:\n", maxsplit=1)[1]

    assert re.findall(r"(?m)^  [a-zA-Z0-9_-]+:\s*$", jobs) == ["  test:"]
    assert "permissions: contents: read" in normalized
    assert "persist-credentials: false" in normalized
    assert "python-version: \"3.12\"" in normalized
    assert 'JARVIS_LIVE_TESTS: "0"' in normalized
    assert 'JARVIS_LIVE_MUTATIONS: "0"' in normalized
    assert "ruff check onshape_mcp tests" in normalized
    assert "python -m mypy onshape_mcp/api/request_guard.py" in normalized
    assert 'python -m pytest -m "not live_onshape" -q --maxfail=1' in normalized
    assert "--cov=onshape_mcp" in normalized
    assert "--cov-branch" in normalized
    assert "--cov-fail-under=80" in normalized

    forbidden = (
        "continue-on-error",
        "strategy:",
        "matrix:",
        "secrets.",
        "ONSHAPE_",
        "JARVIS_LIVE_TESTS: \"1\"",
        "JARVIS_LIVE_MUTATIONS: \"1\"",
        'live_onshape and',
        "codecov",
        "upload-artifact",
        "workflow_dispatch",
        "schedule:",
    )
    for value in forbidden:
        assert value not in workflow


def test_readmes_document_exact_offline_and_coverage_commands() -> None:
    commands = (
        'python -m pytest -m "not live_onshape" -q --maxfail=1',
        "python -m pytest tests/test_test_harness.py -q --maxfail=1",
        'python -m pytest -m "not live_onshape" --cov=onshape_mcp --cov-branch '
        "--cov-report=term-missing --cov-fail-under=80",
    )
    for readme in ("README.md", "tests/README.md"):
        text = _read(readme)
        for command in commands:
            assert command in text
        assert "credential-free" in text.lower()
        assert "offline" in text.lower()


def test_readmes_document_guarded_local_live_contract_without_private_values() -> None:
    placeholders = (
        "<access-key>",
        "<secret-key>",
        "<document-id>",
        "<workspace-id>",
        "<element-id>",
    )
    required = (
        "live_onshape and live_readonly",
        "live_onshape and live_mutation",
        "JARVIS_LIVE_TESTS",
        "JARVIS_LIVE_MUTATIONS",
        "JARVIS_LIVE_DOCUMENT_ID",
        "JARVIS_LIVE_WORKSPACE_ID",
        "JARVIS_LIVE_ELEMENT_ID",
        "JARVIS_LIVE_SUITE_BUDGET",
        "30",
        "serial",
        "guarded transport",
        "exactly one complete credential pair",
        "WP-003 ships no live mutation scenario",
    )

    for readme in ("README.md", "tests/README.md"):
        text = _read(readme)
        lowered = text.lower()
        for value in placeholders:
            assert value in text
        for value in required:
            assert value.lower() in lowered
        assert "set -euo pipefail" in text
        assert "$LASTEXITCODE" in text
        assert "exit $exitCode" in text
        assert "finally" in lowered
        assert "$apiPresent" in text
        assert "$accessPresent" in text
        assert "$apiPresent -and -not $apiComplete" in text
        assert "$accessPresent -and -not $accessComplete" in text
        assert text.index("$apiPresent -and -not $apiComplete") < text.index(
            "$env:JARVIS_LIVE_TESTS = '1'"
        )
        assert text.index("$accessPresent -and -not $accessComplete") < text.index(
            "$env:JARVIS_LIVE_TESTS = '1'"
        )
