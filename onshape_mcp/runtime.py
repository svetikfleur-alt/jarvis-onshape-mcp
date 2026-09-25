"""Canonical runtime configuration and non-secret diagnostics for Jarvis MCP."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
import os
from pathlib import Path
import platform
import sys
from typing import Any, Mapping, Optional

from dotenv import load_dotenv


PACKAGE_NAME = "onshape-mcp"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"
SERVER_MODULE_PATH = Path(__file__).resolve().with_name("server.py")


def load_runtime_environment() -> bool:
    """Load only the canonical repository-root ``.env`` without overriding the process."""

    return bool(load_dotenv(DOTENV_PATH, override=False))


def _package_runtime_metadata() -> tuple[str, Optional[str]]:
    try:
        installed = distribution(PACKAGE_NAME)
    except PackageNotFoundError:
        return "not_installed", None
    return installed.version, str(Path(installed.locate_file("")).resolve())


def get_runtime_info(
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Return a bounded diagnostic without credential values or network access."""

    from .api.client import describe_onshape_credential_configuration

    package_version, distribution_root = _package_runtime_metadata()
    source = os.environ if environment is None else environment
    return {
        "server_status": "initialized",
        "package": {
            "name": PACKAGE_NAME,
            "version": package_version,
        },
        "runtime": {
            "server_module": str(SERVER_MODULE_PATH),
            "project_root": str(PROJECT_ROOT),
            "distribution_root": distribution_root,
            "dotenv_path": str(DOTENV_PATH),
            "dotenv_present": DOTENV_PATH.is_file(),
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "python_prefix": sys.prefix,
            "python_base_prefix": sys.base_prefix,
        },
        "credential_configuration": describe_onshape_credential_configuration(source),
        "network_auth_status": "not_tested",
        "live_authenticated_read_status": "not_tested",
    }
