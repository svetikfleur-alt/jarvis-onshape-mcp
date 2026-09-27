# Create Document Live Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the live `create_document` wire contract, Free-plan policy,
and ambiguous-timeout feedback loop, with offline plus opt-in live coverage.

**Architecture:** Keep document creation in the existing MCP handler →
`DocumentManager` → `OnshapeClient` path. Preserve the low-level tri-state
public/private flag. At the autonomous MCP boundary, cache the documented
Onshape session `planGroup`; confirmed Free accounts explicitly create public
documents while unknown plans preserve omission. Reconcile a create POST
`ReadTimeout` through bounded exact-name searches and one authoritative reread,
never by repeating the POST.

**Tech Stack:** Python 3.10+, MCP, HTTPX, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-13-wp-003-test-harness-live-acceptance-design.md`, specialized by the current user request for a `create_document` live-mutation canary.

## Global Constraints

- Modify only `create_document`, directly related request/error code, directly related tests/harness, and minimal live-test documentation.
- Preserve ordinary CI as credential-free and no-network.
- Live mutation requires explicit `JARVIS_LIVE_TESTS=1` and `JARVIS_LIVE_MUTATIONS=1` opt-ins.
- The L3 canary creates one uniquely named disposable document, never targets a configured pre-existing document, and does not delete its result.
- The L3 canary has a five-physical-send ceiling covering cached session plan
  resolution plus creation and authoritative verification.
- Do not push, merge, delete a remote document, or touch another worktree.

## Review Focus

- Omitted `isPublic` remains tri-state at the low-level manager. The autonomous
  MCP path sends `true` only when session information confirms `planGroup=Free`.
- An explicit `isPublic` value must still be forwarded unchanged.
- The MCP tool schema must not reintroduce a false default that turns omission into an explicit private-document request.
- HTTP 409 must be classified as a sanitized HTTP rejection, not diagnosed as bad credentials.
- A timed-out create POST is never retried. Exactly one credible recent match
  verifies creation; zero or multiple matches remain unverified.
- The live canary must never use configured pre-existing model IDs and must stop
  after five physical sends.

---

### Task 1: Strict offline `create_document` boundary contract

**Files:**
- Create: `tests/test_create_document_contract.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `server.call_tool(name: str, arguments: dict)` and `DocumentManager.create_document(...)`.
- Produces: an L0 schema assertion plus L2 request/response and rejection contracts over `httpx.MockTransport`.

- [ ] **Step 1: Write the failing default-wire-contract test**

Build a real `OnshapeClient` over `httpx.MockTransport`, inject a real `DocumentManager` into the MCP server, call `create_document` with only `name`, and assert the first request is exactly:

```python
assert request.method == "POST"
assert request.url.path == "/api/v10/documents"
assert request.headers["accept"] == "application/json;charset=UTF-8; qs=0.09"
assert request.headers["content-type"] == "application/json;charset=UTF-8; qs=0.09"
assert json.loads(request.content) == {"name": "Jarvis Contract Canary"}
```

Return complete synthetic document/workspace/element responses and assert the MCP result parses their IDs and names.

- [ ] **Step 2: Write the failing schema and dispatch tests**

Assert `create_document.inputSchema.properties.isPublic` has no `default`, and an omitted MCP argument reaches the manager as `is_public=None`.

- [ ] **Step 3: Write the failing HTTP 409 classification test**

Have the mock transport return HTTP 409 with a poison response body. Assert the MCP response is structured JSON with `ok=false`, `failure_kind="http_rejection"`, `status_code=409`, `method="POST"`, route `/api/v10/documents`, `mutation_verification="failed"`, and no credential diagnosis or poison body.

- [ ] **Step 4: Run the new tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_create_document_contract.py tests/test_server.py::TestCreateDocumentTool -q --basetemp .\temp_pytest_create_document_red`

Expected: FAIL because the pre-repair request includes `isPublic: false`, the schema advertises that false default, dispatch supplies `False`, and the 409 handler returns misleading free text.

### Task 2: Minimal production repair

**Files:**
- Modify: `onshape_mcp/api/documents.py`
- Modify: `onshape_mcp/server.py`

**Interfaces:**
- Consumes: optional MCP `isPublic` input.
- Produces: `DocumentManager.create_document(name, description=None, is_public: Optional[bool]=None)` and structured create-document failure output.

- [ ] **Step 1: Make public/private creation tri-state**

Use the minimal implementation:

```python
async def create_document(
    self,
    name: str,
    description: Optional[str] = None,
    is_public: Optional[bool] = None,
) -> DocumentInfo:
    data: Dict[str, Any] = {"name": name}
    if description is not None:
        data["description"] = description
    if is_public is not None:
        data["isPublic"] = is_public
```

- [ ] **Step 2: Preserve omission at the MCP boundary**

Remove the schema's `default: false`, explain that omission follows account policy, and dispatch with `arguments.get("isPublic")`.

- [ ] **Step 3: Replace the misleading create error text**

Serialize both HTTP and local failures through `_exception_json(..., tool_name="create_document")` with create-document-specific hints that distinguish 409 policy/payload conflicts from 403 write-permission failures without exposing response bodies.

- [ ] **Step 4: Run the same tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_create_document_contract.py tests/test_server.py::TestCreateDocumentTool -q --basetemp .\temp_pytest_create_document_green`

Expected: PASS.

### Task 3: Opt-in L3 document-creation canary

**Files:**
- Create: `tests/live/test_live_create_document.py`
- Modify: `tests/test_live_scenario_contract.py`
- Modify: `README.md`
- Modify: `tests/README.md`
- Modify: `tests/test_ci_contract.py`

**Interfaces:**
- Consumes: existing live opt-in, credential, network-permit, and physical-send-budget fixtures.
- Produces: `test_live_create_document_canary` marked `live_onshape`,
  `live_mutation`, and `live_budget(5)`.

- [ ] **Step 1: Add deterministic offline tests for canary target naming**

Test a helper with fixed timestamp/token inputs and assert the name is distinctive, bounded, and changes with the token.

- [ ] **Step 2: Add the gated live test**

Generate a unique name, inject `DocumentManager(live_onshape_client)`, and call
MCP `create_document` with only that name so the canary exercises autonomous
plan resolution. On a normal response, require workspace/Part Studio IDs and an
authoritative document reread. On a reconciled timeout, require verified
semantic success and the bounded search/reread route sequence. Never repeat the
POST, request `live_model_ids`, or delete the document.

- [ ] **Step 3: Update local-live documentation and CI documentation contracts**

Document the exact one-file command, opt-ins, five-call ceiling, autonomous
Free-plan behavior, timeout reconciliation, unique-name behavior, authoritative
reread, and no-auto-delete policy.

- [ ] **Step 4: Verify offline collection stays network-free**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_scenario_contract.py tests/test_ci_contract.py tests/test_test_harness.py -q --basetemp .\temp_pytest_create_document_harness`

Expected: PASS; the live canary is collected but skipped unless explicitly selected and enabled.

### Task 4: Final offline and live validation

**Files:**
- No additional production files expected.

**Interfaces:**
- Consumes: completed repair and tests.
- Produces: evidence for the final production/test-defect report.

- [ ] **Step 1: Run focused and surrounding offline tests**

Run the create-document contract, document API tests, server create-document tests, harness policy tests, CI contract tests, and live scenario contract tests with a verified-new worktree-local `--basetemp`.

Expected: PASS.

- [ ] **Step 2: Run the complete offline suite and Ruff**

Run: `.\.venv\Scripts\python.exe -m pytest -m "not live_onshape" -q --basetemp .\temp_pytest_create_document_final`

Run: `.\.venv\Scripts\python.exe -m ruff check onshape_mcp/api/documents.py onshape_mcp/server.py tests/api/test_documents.py tests/test_create_document_contract.py tests/live/test_live_create_document.py tests/test_server.py tests/test_live_scenario_contract.py tests/test_ci_contract.py`

Expected: PASS.

- [ ] **Step 3: Run one explicitly authorized live canary**

Select only `tests/live/test_live_create_document.py` with `-m "live_onshape and live_mutation"`, set both live opt-ins, retain the configured credential pair, omit pre-existing model IDs, and set a five-request suite budget. Run serially with output enabled so the created name and document ID can be reported.

Expected: one session GET, one POST, and bounded authoritative verification;
the reread matches the returned ID/name; no deletion call.

- [ ] **Step 4: Inspect final diff and status**

Confirm the delta is limited to the files above, no credentials/private response bodies were recorded, no pre-existing document was targeted, and no unrelated changes appeared.
