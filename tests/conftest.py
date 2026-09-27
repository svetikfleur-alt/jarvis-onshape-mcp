"""Pytest configuration, offline policy, and shared fixtures."""

import ast
import os
import pytest
import pytest_asyncio
from unittest.mock import Mock, AsyncMock
import httpx
from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.request_guard import BudgetedAsyncTransport, LiveBudgetGuard, LiveSuiteBudget
from tests.support.live_config import LiveConfigurationError, load_live_config
from tests.support.network_guard import (
    _permit_guarded_network,
    install_network_guard,
    uninstall_network_guard,
)


def pytest_configure(config):
    for marker in (
        "contract: boundary contract test",
        "mcp: MCP boundary test",
        "live_onshape: explicitly selected live Onshape test",
        "live_readonly: read-only live test",
        "live_mutation: mutating live test",
        "live_create_document: create-only live mutation canary",
        "live_budget(limit): lower a live test physical-send budget",
    ):
        config.addinivalue_line("markers", marker)
    network_patches = pytest.MonkeyPatch()
    install_network_guard(network_patches)
    config._jarvis_network_patches = network_patches
    config._jarvis_live_session_telemetry = []
    config._jarvis_live_session_report = False


def pytest_unconfigure(config):
    network_patches = getattr(config, "_jarvis_network_patches", None)
    if network_patches is not None:
        try:
            network_patches.undo()
        finally:
            uninstall_network_guard()
            config._jarvis_network_patches = None


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not config._jarvis_live_session_report:
        return
    events = config._jarvis_live_session_telemetry
    terminalreporter.section("live request telemetry")
    for event in events:
        terminalreporter.write_line(
            f"{event.test_identifier} {event.method} {event.host} {event.route} "
            f"test={event.test_used}/{event.test_limit} "
            f"suite={event.suite_used}/{event.suite_limit} "
            f"blocked={event.blocked_scope}"
        )
    physical_sends = sum(event.blocked_scope == "none" for event in events)
    blocked_attempts = len(events) - physical_sends
    terminalreporter.write_line(
        f"physical_sends={physical_sends} blocked_attempts={blocked_attempts}"
    )


def _parse_marker_expression(markexpr):
    if not markexpr:
        return None
    try:
        return ast.parse(markexpr, mode="eval").body
    except SyntaxError:
        return None


def _has_positive_live_selection(markexpr):
    expression = _parse_marker_expression(markexpr)

    def contains(node, negated=False):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return contains(node.operand, not negated)
        if isinstance(node, ast.Name):
            return node.id == "live_onshape" and not negated
        return any(contains(child, negated) for child in ast.iter_child_nodes(node))

    return expression is not None and contains(expression)


def _can_select_live_item(markexpr):
    expression = _parse_marker_expression(markexpr)
    if expression is None:
        return True

    def possible_values(node):
        if isinstance(node, ast.Name):
            return {True} if node.id == "live_onshape" else {False, True}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return {not value for value in possible_values(node.operand)}
        if isinstance(node, ast.BoolOp):
            child_values = [possible_values(value) for value in node.values]
            if isinstance(node.op, ast.And):
                values = set()
                if all(True in child for child in child_values):
                    values.add(True)
                if any(False in child for child in child_values):
                    values.add(False)
                return values
            if isinstance(node.op, ast.Or):
                values = set()
                if any(True in child for child in child_values):
                    values.add(True)
                if all(False in child for child in child_values):
                    values.add(False)
                return values
        return {True}

    return True in possible_values(expression)


def _fail(code):
    raise pytest.UsageError(code)


def pytest_collection_modifyitems(config, items):
    markexpr = config.option.markexpr or ""
    positive_live = _has_positive_live_selection(markexpr)
    live_items = [item for item in items if item.get_closest_marker("live_onshape")]
    for item in items:
        live = item.get_closest_marker("live_onshape") is not None
        readonly = item.get_closest_marker("live_readonly") is not None
        mutation = item.get_closest_marker("live_mutation") is not None
        create_document = item.get_closest_marker("live_create_document") is not None
        budget = item.get_closest_marker("live_budget")
        if live != (readonly or mutation) or (readonly and mutation) or (budget and not live):
            _fail("LIVE_MARKERS_INVALID")
        if create_document and not (live and mutation and not readonly):
            _fail("LIVE_MARKERS_INVALID")
        if budget:
            if len(budget.args) != 1 or not isinstance(budget.args[0], int) or budget.args[0] <= 0:
                _fail("LIVE_BUDGET_INVALID")
            if budget.args[0] > (8 if mutation else 3):
                _fail("LIVE_BUDGET_INVALID")
    if not live_items:
        return
    if os.getenv("JARVIS_LIVE_TESTS") != "1":
        for item in live_items:
            item.add_marker(pytest.mark.skip(reason="live tests require JARVIS_LIVE_TESTS=1"))
        return
    if not positive_live and not _can_select_live_item(markexpr):
        return
    if not positive_live:
        _fail("LIVE_POSITIVE_SELECTION_REQUIRED")
    if os.getenv("PYTEST_XDIST_WORKER") or getattr(config.option, "numprocesses", 0):
        _fail("LIVE_PARALLEL_FORBIDDEN")
    create_only = bool(live_items) and all(
        item.get_closest_marker("live_create_document") is not None
        for item in live_items
    )
    config._jarvis_live_requires_model_ids = not create_only
    try:
        load_live_config(require_model_ids=not create_only)
    except LiveConfigurationError as error:
        _fail(str(error))
    config._jarvis_live_session_report = True
    if os.getenv("JARVIS_LIVE_MUTATIONS") != "1":
        for item in live_items:
            if item.get_closest_marker("live_mutation"):
                item.add_marker(pytest.mark.skip(reason="mutation tests require JARVIS_LIVE_MUTATIONS=1"))


@pytest.fixture(scope="session")
def live_config(pytestconfig):
    require_model_ids = getattr(
        pytestconfig, "_jarvis_live_requires_model_ids", True
    )
    return load_live_config(require_model_ids=require_model_ids)


@pytest.fixture(scope="session")
def live_suite_budget(live_config):
    return LiveSuiteBudget(live_config.suite_budget)


@pytest.fixture
def live_budget_guard(request, live_suite_budget, live_session_telemetry):
    mutation = request.node.get_closest_marker("live_mutation") is not None
    limit = 8 if mutation else 3
    marker = request.node.get_closest_marker("live_budget")
    if marker:
        limit = marker.args[0]
    scenario_id = (
        "LIVE-DEEP-READ-01"
        if request.node.name == "test_live_deep_read_01"
        else "LIVE-SCENARIO"
    )
    return LiveBudgetGuard(
        scenario_id,
        limit,
        live_suite_budget,
        event_sink=live_session_telemetry,
    )


@pytest.fixture
def live_model_ids(live_config):
    if not all(
        isinstance(value, str) and value
        for value in (
            live_config.document_id,
            live_config.workspace_id,
            live_config.element_id,
        )
    ):
        raise LiveConfigurationError("LIVE_MODEL_IDS_INVALID")
    return {
        "document_id": live_config.document_id,
        "workspace_id": live_config.workspace_id,
        "element_id": live_config.element_id,
    }


@pytest.fixture(scope="session")
def live_session_telemetry(pytestconfig):
    return pytestconfig._jarvis_live_session_telemetry


@pytest_asyncio.fixture
async def live_onshape_client(live_config, live_budget_guard):
    credentials = OnshapeCredentials(
        access_key=live_config.access_key,
        secret_key=live_config.secret_key,
    )
    inner = httpx.AsyncHTTPTransport(retries=0)
    transport = BudgetedAsyncTransport(inner, live_budget_guard, _permit_guarded_network)
    async with OnshapeClient(credentials, transport=transport) as client:
        yield client


@pytest.fixture
def mock_credentials():
    """Provide mock Onshape credentials."""
    return OnshapeCredentials(
        access_key="test_access_key",
        secret_key="test_secret_key",
        base_url="https://test.onshape.com",
    )


@pytest.fixture
def mock_httpx_client():
    """Provide a mock httpx AsyncClient."""
    mock_client = AsyncMock()

    # Default success response
    mock_response = Mock()
    mock_response.json.return_value = {"success": True}
    mock_response.raise_for_status.return_value = None
    mock_response.status_code = 200  # Add status_code for POST/DELETE error logging
    mock_response.text = ""  # Add text attribute for error logging

    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    mock_client.delete.return_value = mock_response

    return mock_client


@pytest.fixture
def onshape_client(mock_credentials, mock_httpx_client, monkeypatch):
    """Provide a fully configured OnshapeClient with mocked HTTP client."""
    client = OnshapeClient(mock_credentials)
    client._client = mock_httpx_client
    return client


@pytest.fixture
def sample_document_ids():
    """Provide sample document, workspace, and element IDs."""
    return {
        "document_id": "test_doc_123",
        "workspace_id": "test_ws_456",
        "element_id": "test_elem_789",
    }


@pytest.fixture
def sample_feature_response():
    """Provide sample feature API response."""
    return {
        "featureId": "test_feature_id",
        "name": "Test Feature",
        "type": "sketch",
        "suppressed": False,
    }


@pytest.fixture
def sample_variables():
    """Provide sample variable data in the actual Onshape /variables response shape:
    a list of one wrapper containing the variable rows. Earlier fixture was a
    flat list, which masked the get_variables flatten bug."""
    return [
        {
            "variableStudioReference": None,
            "variables": [
                {"name": "width", "expression": "10 in", "description": "Width of the part"},
                {"name": "height", "expression": "5 in", "description": "Height of the part"},
            ],
        }
    ]
