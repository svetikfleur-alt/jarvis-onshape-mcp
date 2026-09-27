"""Autonomous account-policy and timeout truth contracts for create_document."""

import asyncio
from datetime import datetime, timezone
import json

import httpx
import pytest
from unittest.mock import AsyncMock

import onshape_mcp.server as server
from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.documents import DocumentManager


def _credentials() -> OnshapeCredentials:
    return OnshapeCredentials(
        access_key="contract-access",
        secret_key="contract-secret",
        base_url="https://contract.onshape.test",
    )


def _session_info(plan_group: str | None, *, include_plan_group: bool = True) -> dict:
    payload = {
        "id": "contract-user-id",
        "name": "Contract User",
        "href": "https://contract.onshape.test/api/v17/users/contract-user-id",
        "clientId": "contract-client-id",
        "companyPlan": False,
        "oauth2Scopes": 3,
        "role": 0,
        "roles": [],
    }
    if include_plan_group:
        payload["planGroup"] = plan_group
    return payload


def _document(
    document_id: str,
    name: str,
    *,
    public: bool,
    created_at: str | None = None,
) -> dict:
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "id": document_id,
        "name": name,
        "createdAt": timestamp,
        "modifiedAt": timestamp,
        "owner": {"id": "contract-user-id", "name": "Contract User"},
        "public": public,
        "description": None,
        "thumbnail": None,
    }


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
@pytest.mark.parametrize("is_public", [True, False])
async def test_explicit_publicity_wins_without_account_lookup(
    monkeypatch: pytest.MonkeyPatch,
    is_public: bool,
) -> None:
    """Removing the explicit branch would spend a lookup or alter the caller's bool."""
    name = f"Explicit {is_public}"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            pytest.fail("explicit visibility must not need account resolution")
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            return httpx.Response(
                200,
                json=_document("explicit-document-id", name, public=is_public),
            )
        if request.url.path == "/api/v6/documents/explicit-document-id":
            return httpx.Response(
                200,
                json=_document("explicit-document-id", name, public=is_public),
            )
        if request.url.path == "/api/v6/documents/d/explicit-document-id/workspaces":
            return httpx.Response(200, json=[])
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool(
            "create_document",
            {"name": name, "isPublic": is_public},
        )

    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert [request.url.path for request in requests] == [
        "/api/v10/documents",
        "/api/v6/documents/explicit-document-id",
        "/api/v6/documents/d/explicit-document-id/workspaces",
    ]
    assert json.loads(requests[0].content) == {
        "name": name,
        "isPublic": is_public,
    }


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_confirmed_free_account_selects_public_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing Free-plan policy would send the known-live-rejected name-only body."""
    name = "Free Account Contract"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(200, json=_session_info("Free"))
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            return httpx.Response(
                200,
                json=_document("free-document-id", name, public=True),
            )
        if request.url.path == "/api/v6/documents/free-document-id":
            return httpx.Response(
                200,
                json=_document("free-document-id", name, public=True),
            )
        if request.url.path == "/api/v6/documents/d/free-document-id/workspaces":
            return httpx.Response(200, json=[])
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool("create_document", {"name": name})

    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert [request.url.path for request in requests] == [
        "/api/v17/users/sessioninfo",
        "/api/v10/documents",
        "/api/v6/documents/free-document-id",
        "/api/v6/documents/d/free-document-id/workspaces",
    ]
    assert json.loads(requests[1].content) == {"name": name, "isPublic": True}
    assert payload["document_public"] is True


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_unknown_account_does_not_silently_default_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating an absent planGroup as Free would silently broaden visibility."""
    name = "Unknown Account Contract"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(
                200,
                json=_session_info(None, include_plan_group=False),
            )
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            return httpx.Response(
                200,
                json=_document("unknown-document-id", name, public=False),
            )
        if request.url.path == "/api/v6/documents/unknown-document-id":
            return httpx.Response(
                200,
                json=_document("unknown-document-id", name, public=False),
            )
        if request.url.path == "/api/v6/documents/d/unknown-document-id/workspaces":
            return httpx.Response(200, json=[])
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool("create_document", {"name": name})

    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert [request.url.path for request in requests] == [
        "/api/v17/users/sessioninfo",
        "/api/v10/documents",
        "/api/v6/documents/unknown-document-id",
        "/api/v6/documents/d/unknown-document-id/workspaces",
    ]
    assert json.loads(requests[1].content) == {"name": name}


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_free_plan_session_information_is_cached_across_creates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the cache would spend one account lookup per autonomous create."""
    requests: list[httpx.Request] = []
    create_count = 0
    created_documents: dict[str, dict] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(200, json=_session_info("Free"))
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            create_count += 1
            name = json.loads(request.content)["name"]
            document_id = f"cached-document-{create_count}"
            created_documents[document_id] = _document(
                document_id,
                name,
                public=True,
            )
            return httpx.Response(
                200,
                json=created_documents[document_id],
            )
        for document_id, document in created_documents.items():
            if request.url.path == f"/api/v6/documents/{document_id}":
                return httpx.Response(200, json=document)
        if request.url.path.endswith("/workspaces"):
            return httpx.Response(200, json=[])
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        manager = DocumentManager(client)
        monkeypatch.setattr(server, "document_manager", manager)
        first = await server.call_tool("create_document", {"name": "Cached One"})
        second = await server.call_tool("create_document", {"name": "Cached Two"})

    assert json.loads(first[0].text)["ok"] is True
    assert json.loads(second[0].text)["ok"] is True
    assert sum(
        request.url.path == "/api/v17/users/sessioninfo" for request in requests
    ) == 1
    create_requests = [
        request
        for request in requests
        if request.method == "POST" and request.url.path == "/api/v10/documents"
    ]
    assert [json.loads(request.content) for request in create_requests] == [
        {"name": "Cached One", "isPublic": True},
        {"name": "Cached Two", "isPublic": True},
    ]


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_read_timeout_with_exact_recent_match_becomes_verified_without_repost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating a response timeout as failure would hide a proven mutation."""
    name = "Timeout Reconciliation Contract"
    created = _document("reconciled-document-id", name, public=True)
    requests: list[httpx.Request] = []
    search_count = 0
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_count
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(200, json=_session_info("Free"))
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            raise httpx.ReadTimeout("response timed out", request=request)
        if request.url.path == "/api/v6/documents":
            search_count += 1
            assert request.url.params["q"] == name
            assert int(request.url.params["limit"]) <= 20
            return httpx.Response(
                200,
                json={"items": [] if search_count == 1 else [created]},
            )
        if request.url.path == "/api/v6/documents/reconciled-document-id":
            return httpx.Response(200, json=created)
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool("create_document", {"name": name})

    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["mutation_verification"] == "verified"
    assert payload["reconciled"] is True
    assert payload["document_id"] == "reconciled-document-id"
    assert payload["document_name"] == name
    assert payload["document_public"] is True
    assert payload["workspace_id"] is None
    assert payload["part_studio_id"] is None
    assert payload["transport_diagnostic"] == {
        "failure_kind": "transport_failure",
        "error_class": "ReadTimeout",
        "method": "POST",
        "route": "/api/v10/documents",
        "status_code": None,
        "message": "The create request timed out before its result was known.",
    }
    assert [request.method for request in requests].count("POST") == 1
    assert [request.url.path for request in requests] == [
        "/api/v17/users/sessioninfo",
        "/api/v10/documents",
        "/api/v6/documents",
        "/api/v6/documents",
        "/api/v6/documents/reconciled-document-id",
    ]


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_read_timeout_without_match_remains_unverified_without_repost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventing success or retrying after zero matches risks duplicate documents."""
    name = "Timeout No Match Contract"
    requests: list[httpx.Request] = []
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(
                200,
                json=_session_info(None, include_plan_group=False),
            )
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            raise httpx.ReadTimeout("response timed out", request=request)
        if request.url.path == "/api/v6/documents":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool("create_document", {"name": name})

    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["failure_kind"] == "transport_failure"
    assert payload["mutation_verification"] == "unverified"
    assert payload["changed"] is None
    assert payload["diagnostic"]["method"] == "POST"
    assert payload["diagnostic"]["route"] == "/api/v10/documents"
    assert [request.method for request in requests].count("POST") == 1
    assert [request.url.path for request in requests] == [
        "/api/v17/users/sessioninfo",
        "/api/v10/documents",
        "/api/v6/documents",
        "/api/v6/documents",
    ]


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_read_timeout_with_ambiguous_matches_remains_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picking one of two credible exact matches would fabricate identity truth."""
    name = "Timeout Ambiguous Contract"
    matches = [
        _document("ambiguous-document-a", name, public=False),
        _document("ambiguous-document-b", name, public=False),
    ]
    requests: list[httpx.Request] = []
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(
                200,
                json=_session_info(None, include_plan_group=False),
            )
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            raise httpx.ReadTimeout("response timed out", request=request)
        if request.url.path == "/api/v6/documents":
            return httpx.Response(200, json={"items": matches})
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool("create_document", {"name": name})

    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["failure_kind"] == "transport_failure"
    assert payload["mutation_verification"] == "unverified"
    assert [request.method for request in requests].count("POST") == 1
    assert [request.url.path for request in requests] == [
        "/api/v17/users/sessioninfo",
        "/api/v10/documents",
        "/api/v6/documents",
    ]


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_normal_create_reread_failure_remains_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful POST cannot prove requested state when its reread fails."""
    name = "Normal Reread Failure Contract"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(
                200,
                json=_session_info(None, include_plan_group=False),
            )
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            return httpx.Response(
                200,
                json=_document("reread-failure-id", name, public=False),
            )
        if request.url.path == "/api/v6/documents/d/reread-failure-id/workspaces":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v6/documents/reread-failure-id":
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool("create_document", {"name": name})

    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["mutation_verification"] == "unverified"
    assert payload["changed"] is None
    assert payload["reason_code"] == "CREATE_DOCUMENT_REREAD_FAILED"
    assert payload["document_id"] == "reread-failure-id"
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_normal_create_authoritative_publicity_mismatch_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comparable authoritative contradiction must not be reported verified."""
    name = "Normal Reread Mismatch Contract"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            return httpx.Response(
                200,
                json=_document("reread-mismatch-id", name, public=True),
            )
        if request.url.path == "/api/v6/documents/d/reread-mismatch-id/workspaces":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v6/documents/reread-mismatch-id":
            return httpx.Response(
                200,
                json=_document("reread-mismatch-id", name, public=False),
            )
        return httpx.Response(500, json={"message": "unexpected contract route"})

    async with OnshapeClient(
        _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool(
            "create_document",
            {"name": name, "isPublic": True},
        )

    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["mutation_verification"] == "failed"
    assert payload["changed"] is True
    assert payload["reason_code"] == "CREATE_DOCUMENT_REREAD_MISMATCH"
    assert payload["document_id"] == "reread-mismatch-id"
