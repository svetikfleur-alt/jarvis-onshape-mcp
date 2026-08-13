"""Repository-policy contracts for credential-free CI and local live opt-in."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
