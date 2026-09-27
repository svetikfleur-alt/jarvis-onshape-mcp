"""Opt-in L3 acceptance for creating one new disposable Onshape document."""

from datetime import datetime, timezone
import json
import re
from typing import Any
from uuid import uuid4

import pytest

import onshape_mcp.server as server
from onshape_mcp.api.documents import DocumentManager


_ID_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


def _create_document_canary_name(
    now: datetime | None = None,
    token: str | None = None,
) -> str:
    """Return a bounded, distinctive name for a document owned by this test."""
    instant = now or datetime.now(timezone.utc)
    suffix = token or uuid4().hex[:8]
    return f"Jarvis create_document canary {instant:%Y%m%dT%H%M%SZ} {suffix}"


def _decode_create_result(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        pytest.fail("LIVE_CREATE_DOCUMENT_UNSTRUCTURED_FAILURE")
    if not isinstance(payload, dict):
        pytest.fail("LIVE_CREATE_DOCUMENT_RESPONSE_INVALID")
    if payload.get("ok") is not True:
        diagnostic = payload.get("diagnostic")
        diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
        reason = payload.get("reason_code") or "UNKNOWN"
        status = diagnostic.get("status_code") or "UNKNOWN"
        pytest.fail(f"LIVE_CREATE_DOCUMENT_REJECTED reason={reason} http={status}")
    return payload


@pytest.mark.asyncio
@pytest.mark.live_onshape
@pytest.mark.live_mutation
@pytest.mark.live_create_document
@pytest.mark.live_budget(5)
async def test_live_create_document_canary(
    monkeypatch: pytest.MonkeyPatch,
    live_onshape_client: Any,
    live_budget_guard: Any,
    live_session_telemetry: list[Any],
) -> None:
    """Create one new document and authoritatively reread only that document."""
    manager = DocumentManager(live_onshape_client)
    monkeypatch.setattr(server, "document_manager", manager)
    document_name = _create_document_canary_name()

    result = await server.call_tool("create_document", {"name": document_name})
    payload = _decode_create_result(result[0].text)

    document_id = payload.get("document_id")
    workspace_id = payload.get("workspace_id")
    part_studio_id = payload.get("part_studio_id")
    reconciled = payload.get("reconciled") is True
    if not isinstance(document_id, str) or not _ID_SEGMENT.fullmatch(document_id):
        pytest.fail("LIVE_CREATE_DOCUMENT_ID_INVALID")
    if payload.get("document_name") != document_name:
        pytest.fail("LIVE_CREATE_DOCUMENT_NAME_MISMATCH")
    if payload.get("mutation_verification") != "verified":
        pytest.fail("LIVE_CREATE_DOCUMENT_NOT_VERIFIED")
    if payload.get("document_public") is not True:
        pytest.fail("LIVE_CREATE_DOCUMENT_NOT_PUBLIC")

    if reconciled:
        if workspace_id is not None or part_studio_id is not None:
            pytest.fail("LIVE_CREATE_DOCUMENT_RECONCILED_SCOPE_INVALID")
    else:
        if not isinstance(workspace_id, str) or not _ID_SEGMENT.fullmatch(workspace_id):
            pytest.fail("LIVE_CREATE_DOCUMENT_WORKSPACE_MISSING")
        if not isinstance(part_studio_id, str) or not _ID_SEGMENT.fullmatch(part_studio_id):
            pytest.fail("LIVE_CREATE_DOCUMENT_PART_STUDIO_MISSING")

    if not 4 <= live_budget_guard.used <= 5 or live_budget_guard.blocked != 0:
        pytest.fail("LIVE_CREATE_DOCUMENT_REQUEST_COUNT_INVALID")
    observed = [(event.method, event.route) for event in live_session_telemetry]
    assert observed[:2] == [
        ("GET", "/api/v17/users/sessioninfo"),
        ("POST", "/api/v10/documents"),
    ]
    assert sum(method == "POST" for method, _route in observed) == 1
    if reconciled:
        assert 1 <= len(observed[2:-1]) <= 2
        assert all(
            item == ("GET", "/api/v6/documents") for item in observed[2:-1]
        )
        assert observed[-1] == ("GET", "/api/v6/documents/{documentId}")
    else:
        assert observed[2:] == [
            ("GET", "/api/v6/documents/{documentId}"),
            ("GET", "/api/v6/documents/d/{documentId}/workspaces"),
            ("GET", "/api/v6/documents/d/{documentId}/w/{workspaceId}/elements"),
        ]
    assert all(event.blocked_scope == "none" for event in live_session_telemetry)

    print(
        "LIVE_CREATE_DOCUMENT_RESULT "
        f"name={document_name!r} document_id={document_id} "
        f"public=true reconciled={reconciled} "
        f"physical_sends={live_budget_guard.used}"
    )
