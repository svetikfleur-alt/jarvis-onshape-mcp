# WP-003 Test Harness and Local Live Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normal tests deterministically offline and add a bounded, local-only Onshape read-acceptance scenario that cannot leak private CAD data or exceed its physical-send budget.

**Architecture:** A supported HTTPX transport wrapper reserves live budgets and temporarily enters a module-private task-local network permit around an injected transport. A pytest plugin blocks all other outbound socket/DNS access, validates live marker/environment policy, and builds the guarded live client. Synthetic fixtures drive full MCP replay without network access.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, HTTPX supported custom transports, Pydantic, GitHub Actions.

## Global Constraints

- Do not execute live tests.
- Do not add a GitHub Actions live workflow, credentials, sandbox IDs, or a mutating live scenario.
- Do not implement missing WP-002 Deep Read capabilities.
- `BudgetedAsyncTransport` wraps an injected supported `httpx.AsyncBaseTransport`.
- The real live fixture uses `httpx.AsyncHTTPTransport(retries=0)`; tests may use deterministic injected transports.
- Do not use or monkey-patch private HTTPX methods.
- Live opt-in never disables the process-wide socket/DNS guard.
- The task-local permit is a module-private capability token, not a cryptographic security boundary.
- Telemetry and logs contain no headers, credentials, query values, bodies, or private CAD identifiers.
- Revision identity is validated only when included in the single authoritative read; otherwise `None`/unknown is valid.
- Request defaults are read-only 3, mutation 8, and suite 30; `live_budget(N)` can only lower a per-test default.
- The overflowing physical send is rejected before the injected transport executes.

---

### Task 1: Atomic Request Budgets, Sanitization, and Supported Transport Wrapper

**Files:**
- Create: `onshape_mcp/api/request_guard.py`
- Create: `tests/api/test_request_guard.py`
- Modify: `onshape_mcp/api/client.py`
- Modify: `tests/api/test_client.py`

**Interfaces:**
- Produces: `sanitize_request(method: str, url: httpx.URL) -> RequestDescriptor`
- Produces: `LiveSuiteBudget(limit: int)` and `LiveBudgetGuard(test_name: str, test_limit: int, suite_budget: LiveSuiteBudget)`
- Produces: `LiveApiBudgetExceeded` with code `LIVE_API_BUDGET_EXCEEDED` and only sanitized fields.
- Produces: `BudgetedAsyncTransport(inner: httpx.AsyncBaseTransport, guard: LiveBudgetGuard, permit_factory: Callable[[], ContextManager[None]] = nullcontext)`.
- Produces: `OnshapeClient(credentials, *, transport: httpx.AsyncBaseTransport | None = None)`.

- [ ] **Step 1: Write failing sanitizer and budget tests**

Add literal expectations covering named ID placeholders, `{opaque}`, discarded
queries/fragments, read-only and suite overflow, and concurrent reservations.

```python
def test_overflow_is_rejected_without_incrementing_underlying_send_count():
    suite = LiveSuiteBudget(limit=30)
    guard = LiveBudgetGuard("LIVE-DEEP-READ-01", 1, suite)
    inner = CountingTransport()
    transport = BudgetedAsyncTransport(inner, guard)
    client = httpx.AsyncClient(transport=transport, follow_redirects=True)
    # First call reaches inner; second raises LIVE_API_BUDGET_EXCEEDED.
    # Assert inner.calls == 1 and the exception contains no ID/query canaries.
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/api/test_request_guard.py -q --maxfail=1 --no-cov
```

Expected: collection/import failure because `request_guard` does not exist.

- [ ] **Step 3: Implement atomic budgets and safe descriptors**

Use one `threading.Lock` inside `LiveSuiteBudget` for suite and per-test counts.
Blocked attempts increment a separate blocked counter but not physical-send
usage. Build exception text solely from `RequestDescriptor` and counts.

- [ ] **Step 4: Verify budget and sanitizer GREEN**

Run the focused test file and require all cases to pass.

- [ ] **Step 5: Write failing supported-transport tests**

Cover:

```python
async def test_redirect_reserves_once_per_inner_transport_invocation(): ...
async def test_redirect_overflow_blocks_before_second_inner_invocation(): ...
async def test_failed_inner_transport_consumes_one_reservation(): ...
async def test_explicit_retry_consumes_one_reservation_per_attempt(): ...
async def test_concurrent_overflow_allows_exactly_n_inner_invocations(): ...
```

The redirect fake returns a literal 302 then 200. The failing transport raises
`httpx.ConnectError`. The retry is a caller loop that reissues the request; do
not add a production retry feature.

- [ ] **Step 6: Implement `BudgetedAsyncTransport` minimally**

Subclass supported `httpx.AsyncBaseTransport`, call `guard.reserve()` before
the injected `inner.handle_async_request()`, scope `permit_factory()` only
around that delegate, and delegate `aclose()`.

- [ ] **Step 7: Integrate transport injection and sanitized diagnostics**

Write client tests first. Then add the optional constructor transport and use
it consistently in context-manager and lazy-client construction. Preserve
structured status/error information while removing raw URL, params, body, and
response-body logging.

- [ ] **Step 8: Run focused client/guard tests and commit**

```powershell
python -m pytest tests/api/test_request_guard.py tests/api/test_client.py -q --maxfail=1 --no-cov
git add onshape_mcp/api/request_guard.py onshape_mcp/api/client.py tests/api/test_request_guard.py tests/api/test_client.py
git commit -m "test: enforce live request budgets at transport boundary"
```

### Task 2: Pytest Offline Network Guard and Live Policy

**Files:**
- Create: `tests/support/network_guard.py`
- Create: `tests/support/live_config.py`
- Create: `tests/test_test_harness.py`
- Modify: `tests/conftest.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `BudgetedAsyncTransport`, `LiveSuiteBudget`, and `LiveBudgetGuard` from Task 1.
- Produces: `NETWORK_ACCESS_FORBIDDEN_IN_TEST` failure behavior.
- Produces: module-private `_permit_guarded_network()` context manager used only by the live client fixture.
- Produces: `live_onshape_client`, `live_model_ids`, and session telemetry fixtures.

- [ ] **Step 1: Register markers and write failing policy tests**

Use `pytester` subprocess cases for positive live selection, missing opt-in,
partial/ambiguous credentials, missing IDs, mutation gating, invalid marker
combinations, lowered budgets, and parallel-live refusal.

- [ ] **Step 2: Verify policy tests fail for missing plugin behavior**

```powershell
python -m pytest tests/test_test_harness.py -q --maxfail=1 --no-cov
```

- [ ] **Step 3: Implement live configuration parsing and hooks**

Accept exactly one complete credential pair. Validate each sandbox ID as one
bounded URL segment. Skip selected live tests when `JARVIS_LIVE_TESTS` is not
`1`; after explicit enablement, refuse invalid configuration before fixture
construction. Require category markers and serial execution.

- [ ] **Step 4: Write failing network isolation acceptance test**

Start a loopback server (bind/listen remain permitted). With a fully simulated
live environment, construct an `OnshapeClient` using
`BudgetedAsyncTransport(AsyncHTTPTransport(retries=0), ..., _permit_guarded_network)`.
Assert one guarded request succeeds and is counted, then assert direct HTTPX,
socket, and DNS attempts fail with `NETWORK_ACCESS_FORBIDDEN_IN_TEST`.

- [ ] **Step 5: Implement the socket/DNS guard**

Patch outbound socket connect/send entry points and synchronous/async DNS
entry points to consult a private `ContextVar` capability. Do not expose a
global enable switch. Always reset the token in `finally`.

- [ ] **Step 6: Verify the focused harness tests and regression subset**

```powershell
python -m pytest tests/test_test_harness.py tests/api/test_request_guard.py tests/api/test_client.py -q --maxfail=1 --no-cov
```

If guarded loopback access cannot coexist with direct-access denial without a
broad network toggle or invasive transport rewrite, stop and report.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml tests/conftest.py tests/support tests/test_test_harness.py
git commit -m "test: block unguarded network access by default"
```

### Task 3: Synthetic Fixtures and Full MCP Replay

**Files:**
- Create: `tests/fixtures/partstudio/small_clean/metadata.json`
- Create: `tests/fixtures/partstudio/small_clean/features.json`
- Create: `tests/fixtures/partstudio/unresolved_geometry_refs/metadata.json`
- Create: `tests/fixtures/partstudio/unresolved_geometry_refs/features.json`
- Create: `tests/test_mcp_replay.py`
- Modify: `.gitignore`
- Modify: `tests/test_deep_read_tools.py`

**Interfaces:**
- Consumes: Task 1 guarded transport and current WP-002 MCP tools.
- Produces: reusable `partstudio_fixture(name: str) -> dict` test fixture.

- [ ] **Step 1: Add synthetic fixtures with provenance**

Metadata contains literal `source`, `purpose`, `feature_count`, and
`privacy_status: "synthetic-public"`. Feature fixtures include complete
document structure plus unique raw poison canaries. Ignore
`tests/.local-fixtures/`.

- [ ] **Step 2: Write failing full-route replay test**

Use a real `OnshapeClient`, `PartStudioManager`, `BudgetedAsyncTransport`, and
`httpx.MockTransport`; patch only the server's manager/context globals. Call
`start_model_context`, compact tree, search, inspection, dependency slice, and
repeat inspection. Assert one inner transport invocation and exact ledger
counts, not merely mock call presence.

- [ ] **Step 3: Verify RED, then implement only fixture/test helpers**

```powershell
python -m pytest tests/test_mcp_replay.py -q --maxfail=1 --no-cov
```

Do not add missing Deep Read production behavior. If an expected WP-002 tool is
absent, stop and report.

- [ ] **Step 4: Add deterministic revision and leakage cases**

Run the same route once with `sourceMicroversion` and once without it. Assert
propagation when present and explicit `None` when absent. Scan every returned
envelope for all poison canaries and for accidental complete `features` copies.

- [ ] **Step 5: Extend governance hard tests**

Cover page/byte caps, unresolved-reference evidence, hypothesis gate before
upstream access, and repeated cached inspection with zero extra sends.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest tests/test_mcp_replay.py tests/test_governance.py tests/test_feature_intelligence.py tests/test_deep_read_tools.py -q --maxfail=1 --no-cov
git add .gitignore tests/fixtures tests/test_mcp_replay.py tests/test_deep_read_tools.py
git commit -m "test: add deterministic MCP deep-read replay"
```

### Task 4: Local-only LIVE-DEEP-READ-01

**Files:**
- Create: `tests/live/__init__.py`
- Create: `tests/live/test_live_deep_read.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: live configuration, guarded client, model IDs, telemetry, and current WP-002 tools.
- Produces: one collectable read-only live acceptance test with a one-send budget.

- [ ] **Step 1: Write the live scenario without running it**

Mark it `live_onshape`, `live_readonly`, and `live_budget(1)`. Patch the server
to use a real `PartStudioManager` backed by `live_onshape_client` and a fresh
`ContextStore`. Use fixed `pytest.fail()` messages rather than assertions that
would print private payloads.

- [ ] **Step 2: Implement deterministic CAD predicates**

Require nonempty normalized tree/search/inspection output, an inspected feature
with `feature_status == "OK"`, bounded serialized envelopes, referentially
valid dependency edges, no raw feature document, and one transport request.

- [ ] **Step 3: Implement conditional revision assertion**

If the first response reports a revision, require the same value throughout
the context. Otherwise require explicit `None`/unknown and make no extra call.

- [ ] **Step 4: Collect only and commit**

```powershell
python -m pytest --collect-only -m "live_onshape and live_readonly" -q
git add tests/conftest.py tests/live
git commit -m "test: add local read-only Onshape acceptance scenario"
```

Do not run the collected test.

### Task 5: Developer Commands, Coverage, and Credential-free PR CI

**Files:**
- Modify: `tests/README.md`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Delete: `.coveragerc`
- Modify: `.github/workflows/test.yml`

**Interfaces:**
- Produces: focused/offline/coverage/live Bash and PowerShell command contracts.
- Produces: blocking Ruff, offline coverage ≥80%, and focused mypy PR checks.

- [ ] **Step 1: Update pytest and coverage configuration**

Remove automatic coverage flags from pytest `addopts`. Keep branch coverage and
`fail_under = 80` in `pyproject.toml`; delete the competing `.coveragerc`.

- [ ] **Step 2: Document commands and environment contract**

Document the exact offline commands plus Bash and PowerShell live read-only and
mutation examples. Use placeholders only for user-supplied environment values;
never provide or echo credentials/IDs. State that the mutation harness exists
but WP-003 has no mutation scenario.

- [ ] **Step 3: Strengthen PR CI**

Run Ruff, `python -m mypy onshape_mcp/api/request_guard.py`, and one credential-
free offline pytest coverage job using `-m "not live_onshape"` and
`--cov-fail-under=80`. Explicitly set both live flags to `0`. Add no live job.

- [ ] **Step 4: Run static and focused config verification, then commit**

```powershell
ruff check onshape_mcp tests
python -m pytest tests/test_test_harness.py -q --maxfail=1
git add .github/workflows/test.yml pyproject.toml README.md tests/README.md .coveragerc
git commit -m "ci: enforce credential-free offline test gates"
```

### Task 6: Final Offline Verification and Completion Audit

**Files:**
- Modify only files required to address verified failures.

- [ ] **Step 1: Run Ruff**

```powershell
ruff check onshape_mcp tests
```

- [ ] **Step 2: Run the mandatory offline gate once**

```powershell
python -m pytest -m "not live_onshape" -q --maxfail=1
```

- [ ] **Step 3: Run coverage once near completion**

```powershell
python -m pytest -m "not live_onshape" --cov=onshape_mcp --cov-report=term-missing --cov-fail-under=80
```

- [ ] **Step 4: Run focused type checking**

```powershell
python -m mypy onshape_mcp/api/request_guard.py
```

- [ ] **Step 5: Collect the live scenario without executing it**

```powershell
python -m pytest --collect-only -m "live_onshape and live_readonly" -q
```

- [ ] **Step 6: Audit requirements and report**

Review every WP-003 acceptance criterion against code or fresh test evidence.
Report changed files, architecture, network-guard result, exact offline and
coverage results, `LIVE TESTS EXECUTED? no`, live request count as not
applicable, and known gaps.
