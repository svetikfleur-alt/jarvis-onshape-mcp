"""Enhanced MCP server for Onshape programmatic CAD modeling."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("onshape-mcp")
except PackageNotFoundError:
    __version__ = "not_installed"
