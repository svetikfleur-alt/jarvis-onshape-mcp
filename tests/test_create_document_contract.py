"""Boundary contracts for the create_document MCP path."""

import json

import httpx
import pytest

import onshape_mcp.server as server
from onshape_mcp.api.client import OnshapeClient, OnshapeCredentials
from onshape_mcp.api.documents import DocumentManager


def _credentials() -> OnshapeCredentials:
    return OnshapeCredentials(
        access_key="contract-access",
        secret_key="contract-secret",
        base_url="https://contract.onshape.test",
    )


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_create_document_default_matches_authoritative_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting isPublic must send only the fields the caller supplied."""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(
                200,
                json={
                    "id": "owner-id",
                    "name": "Contract Owner",
                    "planGroup": None,
                },
            )
        if request.method == "POST" and request.url.path == "/api/v10/documents":
            return httpx.Response(
                200,
                json={
                    "id": "new-document-id",
                    "name": "Jarvis Contract Canary",
                    "createdAt": "2026-09-25T12:00:00Z",
                    "modifiedAt": "2026-09-25T12:00:00Z",
                    "owner": {"id": "owner-id", "name": "Contract Owner"},
                    "public": True,
                    "description": None,
                },
            )
        if request.url.path == "/api/v6/documents/new-document-id":
            return httpx.Response(
                200,
                json={
                    "id": "new-document-id",
                    "name": "Jarvis Contract Canary",
                    "createdAt": "2026-09-25T12:00:00Z",
                    "modifiedAt": "2026-09-25T12:00:00Z",
                    "owner": {"id": "owner-id", "name": "Contract Owner"},
                    "public": True,
                    "description": None,
                },
            )
        if request.url.path == "/api/v6/documents/d/new-document-id/workspaces":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "main-workspace-id",
                        "name": "Main",
                        "isMain": True,
                        "createdAt": "2026-09-25T12:00:00Z",
                        "modifiedAt": "2026-09-25T12:00:00Z",
                    }
                ],
            )
        if (
            request.url.path
            == "/api/v6/documents/d/new-document-id/w/main-workspace-id/elements"
        ):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "default-part-studio-id",
                        "name": "Part Studio 1",
                        "type": "PARTSTUDIO",
                    }
                ],
            )
        return httpx.Response(500, json={"message": "unexpected contract route"})

    transport = httpx.MockTransport(handler)
    async with OnshapeClient(_credentials(), transport=transport) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool(
            "create_document", {"name": "Jarvis Contract Canary"}
        )

    payload = json.loads(result[0].text)
    assert payload == {
        "ok": True,
        "document_id": "new-document-id",
        "document_name": "Jarvis Contract Canary",
        "workspace_id": "main-workspace-id",
        "part_studio_id": "default-part-studio-id",
        "part_studio_name": "Part Studio 1",
        "tool": "create_document",
        "mutation_verification": "verified",
        "reconciled": False,
        "document_public": True,
    }
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/v17/users/sessioninfo"),
        ("POST", "/api/v10/documents"),
        ("GET", "/api/v6/documents/new-document-id"),
        ("GET", "/api/v6/documents/d/new-document-id/workspaces"),
        (
            "GET",
            "/api/v6/documents/d/new-document-id/w/main-workspace-id/elements",
        ),
    ]
    create_request = requests[1]
    assert create_request.headers["accept"] == "application/json;charset=UTF-8; qs=0.09"
    assert (
        create_request.headers["content-type"]
        == "application/json;charset=UTF-8; qs=0.09"
    )
    assert json.loads(create_request.content) == {"name": "Jarvis Contract Canary"}


@pytest.mark.asyncio
@pytest.mark.contract
async def test_create_document_schema_preserves_account_policy_omission() -> None:
    tools = await server.list_tools()
    create_tool = next(tool for tool in tools if tool.name == "create_document")

    is_public = create_tool.inputSchema["properties"]["isPublic"]

    assert "default" not in is_public


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.mcp
async def test_create_document_409_is_sanitized_http_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_poison = "PRIVATE_ACCOUNT_POLICY_RESPONSE_BODY"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v17/users/sessioninfo":
            return httpx.Response(200, json={"planGroup": None})
        assert request.method == "POST"
        assert request.url.path == "/api/v10/documents"
        return httpx.Response(409, text=raw_poison)

    transport = httpx.MockTransport(handler)
    async with OnshapeClient(_credentials(), transport=transport) as client:
        monkeypatch.setattr(server, "document_manager", DocumentManager(client))
        result = await server.call_tool(
            "create_document", {"name": "Jarvis Rejection Canary"}
        )

    rendered = result[0].text
    payload = json.loads(rendered)
    assert payload["ok"] is False
    assert payload["status"] == "EXCEPTION"
    assert payload["failure_kind"] == "http_rejection"
    assert payload["reason_code"] == "ONSHAPE_HTTP_REJECTED"
    assert payload["mutation_verification"] == "failed"
    assert payload["changed"] is False
    assert payload["diagnostic"] == {
        "failure_kind": "http_rejection",
        "method": "POST",
        "route": "/api/v10/documents",
        "status_code": 409,
        "category": None,
        "reference_path": None,
        "message": "Onshape rejected the request payload.",
    }
    assert "check your api credentials and permissions" not in rendered.lower()
    assert any(
        "does not by itself establish invalid credentials" in hint
        for hint in payload["hints"]
    )
    assert raw_poison not in rendered
