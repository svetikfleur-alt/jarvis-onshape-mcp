"""Acceptance tests for the offline-by-default pytest harness."""

import asyncio
import _socket
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import httpx
import pytest

from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.request_guard import (
    BudgetedAsyncTransport,
    LiveBudgetGuard,
    LiveRequestEvent,
    LiveSuiteBudget,
)
from tests.conftest import (
    live_session_telemetry,
    pytest_collection_modifyitems,
    pytest_terminal_summary,
)
from tests.support.live_config import load_live_config
from tests.support.network_guard import (
    NETWORK_ACCESS_FORBIDDEN_IN_TEST,
    _SOCKET_SEND_METHODS,
    _permit_guarded_network,
)


pytest_plugins = ("pytester",)

_LIVE_ENV_NAMES = (
    "JARVIS_LIVE_TESTS",
    "JARVIS_LIVE_MUTATIONS",
    "JARVIS_LIVE_DOCUMENT_ID",
    "JARVIS_LIVE_WORKSPACE_ID",
    "JARVIS_LIVE_ELEMENT_ID",
    "JARVIS_LIVE_SUITE_BUDGET",
    "ONSHAPE_ACCESS_KEY",
    "ONSHAPE_SECRET_KEY",
    "ONSHAPE_API_KEY",
    "ONSHAPE_API_SECRET",
    "PYTEST_XDIST_WORKER",
)


class _FakeTerminalReporter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def section(self, title: str) -> None:
        self.lines.append(title)

    def write_line(self, line: str) -> None:
        self.lines.append(line)


class _FakeConfig:
    pass


class _FakeLiveItem:
    name = "test_synthetic_live_scenario"

    def get_closest_marker(self, name: str):
        if name in {"live_onshape", "live_readonly"}:
            return SimpleNamespace(args=())
        return None


def _configure_subprocess(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIVE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("PYTHONUTF8", "1")
    repository = Path(__file__).resolve().parents[1]
    pytester.makeconftest(
        f"""
import sys
sys.path.insert(0, {str(repository)!r})
from tests.conftest import *
"""
    )


def _enable_readonly_live(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIVE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    values = {
        "JARVIS_LIVE_TESTS": "1",
        "ONSHAPE_ACCESS_KEY": "access-canary",
        "ONSHAPE_SECRET_KEY": "secret-canary",
        "JARVIS_LIVE_DOCUMENT_ID": "document-canary",
        "JARVIS_LIVE_WORKSPACE_ID": "workspace-canary",
        "JARVIS_LIVE_ELEMENT_ID": "element-canary",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_enable_readonly_live_replaces_ambient_live_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONSHAPE_API_KEY", "ambient-alternate-key")
    monkeypatch.setenv("ONSHAPE_API_SECRET", "ambient-alternate-secret")
    monkeypatch.setenv("JARVIS_LIVE_SUITE_BUDGET", "invalid-ambient-budget")

    _enable_readonly_live(monkeypatch)
    config = load_live_config()

    assert config.access_key == "access-canary"
    assert config.secret_key == "secret-canary"
    assert config.suite_budget == 30
    assert os.getenv("ONSHAPE_API_KEY") is None
    assert os.getenv("ONSHAPE_API_SECRET") is None


def test_subprocess_helper_forces_utf8_child_output(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)

    _configure_subprocess(pytester, monkeypatch)

    assert os.environ["PYTHONIOENCODING"] == "utf-8"
    assert os.environ["PYTHONUTF8"] == "1"


def test_offline_terminal_summary_emits_no_live_telemetry() -> None:
    config = _FakeConfig()
    config._jarvis_live_session_report = False
    config._jarvis_live_session_telemetry = [
        LiveRequestEvent(
            test_identifier="LIVE-SCENARIO",
            method="GET",
            host="cad.onshape.com",
            route="/api/v9/documents/{documentId}",
            test_used=1,
            test_limit=3,
            suite_used=1,
            suite_limit=30,
            blocked_scope="none",
        )
    ]
    reporter = _FakeTerminalReporter()

    pytest_terminal_summary(reporter, 0, config)

    assert reporter.lines == []


def test_enabled_terminal_summary_emits_only_sanitized_events_and_exact_totals() -> None:
    config = _FakeConfig()
    config._jarvis_live_session_report = True
    config._jarvis_live_session_telemetry = [
        LiveRequestEvent(
            test_identifier="LIVE-DEEP-READ-01",
            method="GET",
            host="cad.onshape.com",
            route="/api/v9/documents/d/{documentId}",
            test_used=1,
            test_limit=1,
            suite_used=1,
            suite_limit=30,
            blocked_scope="none",
        ),
        LiveRequestEvent(
            test_identifier="LIVE-SCENARIO",
            method="GET",
            host="cad.onshape.com",
            route="/api/v9/documents/d/{documentId}",
            test_used=1,
            test_limit=1,
            suite_used=1,
            suite_limit=30,
            blocked_scope="test",
        ),
    ]
    reporter = _FakeTerminalReporter()

    pytest_terminal_summary(reporter, 0, config)

    assert reporter.lines == [
        "live request telemetry",
        "LIVE-DEEP-READ-01 GET cad.onshape.com /api/v9/documents/d/{documentId} "
        "test=1/1 suite=1/30 blocked=none",
        "LIVE-SCENARIO GET cad.onshape.com /api/v9/documents/d/{documentId} "
        "test=1/1 suite=1/30 blocked=test",
        "physical_sends=1 blocked_attempts=1",
    ]
    output = "\n".join(reporter.lines)
    for poison in (
        "private-document-canary",
        "private-access-canary",
        "Authorization",
        "secret=query-canary",
        "response-body-canary",
    ):
        assert poison not in output


def test_live_session_telemetry_fixture_returns_plugin_owned_list() -> None:
    config = _FakeConfig()
    config._jarvis_live_session_telemetry = []

    assert live_session_telemetry.__wrapped__(config) is config._jarvis_live_session_telemetry


def test_live_report_flag_requires_positive_selection_and_valid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_readonly_live(monkeypatch)
    item = _FakeLiveItem()

    offline_config = _FakeConfig()
    offline_config.option = SimpleNamespace(markexpr="not live_onshape", numprocesses=0)
    offline_config._jarvis_live_session_report = False
    pytest_collection_modifyitems(offline_config, [item])
    assert offline_config._jarvis_live_session_report is False

    invalid_config = _FakeConfig()
    invalid_config.option = SimpleNamespace(markexpr="live_onshape", numprocesses=0)
    invalid_config._jarvis_live_session_report = False
    monkeypatch.delenv("ONSHAPE_SECRET_KEY")
    with pytest.raises(pytest.UsageError, match="LIVE_CREDENTIALS_INVALID"):
        pytest_collection_modifyitems(invalid_config, [item])
    assert invalid_config._jarvis_live_session_report is False

    valid_config = _FakeConfig()
    valid_config.option = SimpleNamespace(markexpr="live_onshape", numprocesses=0)
    valid_config._jarvis_live_session_report = False
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "secret-canary")
    pytest_collection_modifyitems(valid_config, [item])
    assert valid_config._jarvis_live_session_report is True


def test_selected_live_readonly_test_reaches_setup_when_fully_configured(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape and live_readonly")

    result.assert_outcomes(passed=1)


def test_selected_live_test_skips_before_setup_without_global_opt_in(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.fixture
def setup_must_not_run():
    raise AssertionError("live setup ran")

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy(setup_must_not_run):
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    result.assert_outcomes(skipped=1)


@pytest.mark.parametrize(
    ("environment", "error_code"),
    [
        ({"ONSHAPE_ACCESS_KEY": "access-canary"}, "LIVE_CREDENTIALS_INVALID"),
        (
            {
                "ONSHAPE_ACCESS_KEY": "access-canary",
                "ONSHAPE_SECRET_KEY": "secret-canary",
                "ONSHAPE_API_KEY": "alternate-access-canary",
                "ONSHAPE_API_SECRET": "alternate-secret-canary",
            },
            "LIVE_CREDENTIALS_AMBIGUOUS",
        ),
    ],
)
def test_enabled_live_run_rejects_incomplete_or_ambiguous_credentials(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    error_code: str,
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    monkeypatch.setenv("JARVIS_LIVE_TESTS", "1")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in (
        "JARVIS_LIVE_DOCUMENT_ID",
        "JARVIS_LIVE_WORKSPACE_ID",
        "JARVIS_LIVE_ELEMENT_ID",
    ):
        monkeypatch.setenv(name, "bounded-id")
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    assert result.ret != 0
    result.stderr.fnmatch_lines([f"*{error_code}*"])


@pytest.mark.parametrize("identifier", ["", "two/segments", "..", "x" * 129])
def test_enabled_live_run_rejects_missing_or_unbounded_model_ids(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    identifier: str,
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    monkeypatch.setenv("JARVIS_LIVE_ELEMENT_ID", identifier)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_MODEL_IDS_INVALID*"])


def test_mutation_test_skips_without_separate_mutation_opt_in(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_mutation
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape and live_mutation")

    result.assert_outcomes(skipped=1)


@pytest.mark.parametrize(
    "markers",
    [
        "pytest.mark.live_onshape",
        "pytest.mark.live_onshape, pytest.mark.live_readonly, pytest.mark.live_mutation",
        "pytest.mark.live_readonly",
        "pytest.mark.live_budget(1)",
    ],
)
def test_invalid_live_marker_combinations_are_rejected(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    markers: str,
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        f"""
import pytest

pytestmark = [{markers}]

def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_MARKERS_INVALID*"])


def test_live_test_must_be_positively_selected(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_POSITIVE_SELECTION_REQUIRED*"])


def test_unrelated_marker_name_does_not_count_as_positive_live_selection(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    pytester.makeini(
        """
[pytest]
addopts = --strict-markers
markers =
    live_onshape_shadow: synthetic non-live marker for exact-name selection
"""
    )
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
@pytest.mark.live_onshape_shadow
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape_shadow")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_POSITIVE_SELECTION_REQUIRED*"])


def test_pure_live_negation_keeps_the_run_offline(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    raise AssertionError("negated live test ran")

def test_offline_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "not live_onshape")

    result.assert_outcomes(passed=1, deselected=1)


def test_mixed_expression_with_only_negated_live_term_stays_offline(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    raise AssertionError("negated live test ran")

@pytest.mark.unit
def test_offline_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "unit and not live_onshape")

    result.assert_outcomes(passed=1, deselected=1)


def test_shadow_disjunction_does_not_supply_positive_live_selection(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
@pytest.mark.live_onshape_shadow
def test_live_policy():
    raise AssertionError("live setup reached")
"""
    )

    result = pytester.runpytest_subprocess(
        "-q", "-m", "not live_onshape or live_onshape_shadow"
    )

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_POSITIVE_SELECTION_REQUIRED*"])


def test_live_budget_marker_can_lower_readonly_default(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
@pytest.mark.live_budget(1)
def test_live_policy(live_budget_guard):
    assert live_budget_guard.test_limit == 1
    assert live_budget_guard.suite_budget.limit == 30
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    result.assert_outcomes(passed=1)


def test_live_budget_marker_cannot_raise_category_default(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
@pytest.mark.live_budget(4)
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_BUDGET_INVALID*"])


def test_parallel_live_execution_is_refused(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    pytester.makepyfile(
        """
import pytest

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_live_policy():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape")

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*LIVE_PARALLEL_FORBIDDEN*"])


def test_live_budget_fixture_emits_sanitized_session_telemetry(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    _enable_readonly_live(monkeypatch)
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "private-access-env-canary")
    pytester.makepyfile(
        """
import httpx
import pytest
from onshape_mcp.api.request_guard import sanitize_request

@pytest.mark.live_onshape
@pytest.mark.live_readonly
def test_private_nodeid_canary(live_budget_guard, live_session_telemetry):
    descriptor = sanitize_request(
        "GET",
        httpx.URL("https://cad.onshape.com/api/v9/documents/d/private-document-canary")
    )
    live_budget_guard.reserve(descriptor)
    assert len(live_session_telemetry) == 1
    event = live_session_telemetry[0]
    assert event.test_identifier == "LIVE-SCENARIO"
    assert event.method == "GET"
    assert event.host == "cad.onshape.com"
    assert event.route == "/api/v9/documents/d/{documentId}"
    assert event.blocked_scope == "none"
    assert "private" not in repr(event)
"""
    )

    result = pytester.runpytest_subprocess("-q", "-m", "live_onshape and live_readonly")

    result.assert_outcomes(passed=1)
    assert "private-access-env-canary" not in result.stdout.str()


def test_live_config_fixture_refuses_construction_without_explicit_enablement(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    pytester.makepyfile(
        """
def test_offline_cannot_construct_live_config(live_config):
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q")

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*LIVE_TESTS_NOT_ENABLED*"])


def test_module_import_cannot_connect_to_loopback(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    pytester.makepyfile(
        """
import socket

socket.socket().connect(("127.0.0.1", 9))

def test_never_collected():
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q")

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*NETWORK_ACCESS_FORBIDDEN_IN_TEST*"])


def test_session_fixture_cannot_resolve_nameinfo(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    pytester.makepyfile(
        """
import socket
import pytest

@pytest.fixture(scope="session")
def forbidden_dns():
    socket.getnameinfo(("127.0.0.1", 80), 0)

def test_dns_policy(forbidden_dns):
    pass
"""
    )

    result = pytester.runpytest_subprocess("-q")

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*NETWORK_ACCESS_FORBIDDEN_IN_TEST*"])


@pytest.mark.asyncio
async def test_guarded_loopback_is_counted_while_direct_network_remains_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_readonly_live(monkeypatch)
    live_config = load_live_config()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve_one_request() -> None:
        connection, _ = server.accept()
        with connection:
            connection.recv(4096)
            body = b'{"ok":true}'
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n"
                b"Connection: close\r\n\r\n" + body
            )

    thread = threading.Thread(target=serve_one_request)
    thread.start()
    suite = LiveSuiteBudget(limit=3)
    guard = LiveBudgetGuard("loopback-policy", 1, suite)
    transport = BudgetedAsyncTransport(
        httpx.AsyncHTTPTransport(retries=0), guard, _permit_guarded_network
    )
    credentials = OnshapeCredentials(
        access_key=live_config.access_key,
        secret_key=live_config.secret_key,
        base_url=f"http://localhost:{port}",
    )
    try:
        async with OnshapeClient(credentials, transport=transport) as client:
            assert await client.get("/api/v9/documents") == {"ok": True}
        assert guard.used == 1
        assert suite.used == 1

        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            with httpx.Client() as direct_client:
                direct_client.get(f"http://127.0.0.1:{port}")
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            socket.socket().connect(("127.0.0.1", port))
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            socket.getaddrinfo("localhost", port)
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            socket.getnameinfo(("127.0.0.1", port), 0)
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
                datagram.sendto(b"forbidden", ("127.0.0.1", port))
        if hasattr(socket.socket, "sendmsg"):
            with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
                    datagram.sendmsg([b"forbidden"], [], 0, ("127.0.0.1", port))
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            await asyncio.get_running_loop().getaddrinfo("localhost", port)
    finally:
        server.close()
        thread.join(timeout=2)


def test_network_guard_declares_every_public_datagram_send_boundary() -> None:
    assert "sendto" in _SOCKET_SEND_METHODS
    assert "sendmsg" in _SOCKET_SEND_METHODS


@pytest.mark.parametrize("socket_factory", [socket.SocketType, _socket.socket])
def test_low_level_socket_aliases_cannot_bypass_connect_policy(socket_factory) -> None:
    direct_socket = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            direct_socket.connect_ex(("127.0.0.1", 9))
    finally:
        direct_socket.close()


def test_low_level_dns_alias_cannot_bypass_network_policy() -> None:
    with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
        _socket.getaddrinfo("localhost", 80)


def test_embedded_pytest_restores_low_level_network_after_unconfigure(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_subprocess(pytester, monkeypatch)
    pytester.makepyfile(
        test_embedded="""
import _socket
import pytest

def test_low_level_network_is_guarded_during_pytest():
    direct_socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="NETWORK_ACCESS_FORBIDDEN_IN_TEST"):
            direct_socket.connect_ex(("127.0.0.1", 9))
    finally:
        direct_socket.close()
"""
    )

    result = pytester.runpython_c(
        """
import _socket
import pytest

exit_code = pytest.main(["-q", "test_embedded.py"])
assert exit_code == pytest.ExitCode.OK
direct_socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
try:
    direct_socket.settimeout(0.2)
    result = direct_socket.connect_ex(("127.0.0.1", 9))
finally:
    direct_socket.close()
print(f"POST_PYTEST_CONNECT_EX={result}")
"""
    )

    assert result.ret == 0
    result.stdout.fnmatch_lines(["*1 passed*", "POST_PYTEST_CONNECT_EX=*"])


@pytest.mark.asyncio
async def test_running_event_loop_sock_connect_is_guarded() -> None:
    direct_socket = socket.socket()
    direct_socket.setblocking(False)
    try:
        with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
            await asyncio.get_running_loop().sock_connect(direct_socket, ("127.0.0.1", 9))
    finally:
        direct_socket.close()


@pytest.mark.asyncio
async def test_importable_base_proactor_sock_connect_override_is_guarded() -> None:
    try:
        from asyncio.proactor_events import BaseProactorEventLoop
    except ImportError:
        pytest.skip("proactor event loops are unavailable on this platform")

    with pytest.raises(RuntimeError, match=NETWORK_ACCESS_FORBIDDEN_IN_TEST):
        await BaseProactorEventLoop.sock_connect(None, None, None)
