# WP-003 Test Harness and Local Live Acceptance Design

## Purpose

WP-003 makes the Jarvis Onshape MCP test stack offline by default and adds a
small, explicitly enabled local live-acceptance layer. Test success must never
depend on accidental network access, HTTP 2xx alone, or an unbounded number of
Onshape requests.

The levels are:

1. L0: static, schema, serialization, and registration contracts.
2. L1: unit tests over pure logic and public synthetic fixtures.
3. L2: complete MCP replay through a fake HTTPX transport.
4. L3: local-only, opt-in Onshape acceptance with hard request budgets.

WP-002 Deep Read is a dependency. WP-003 tests the Deep Read tools already in
the repository; it does not implement missing Deep Read capabilities.

## Non-goals

- No GitHub Actions live workflow.
- No live mutation scenario.
- No credentials, document IDs, workspace IDs, or element IDs in the repo.
- No generalized record/replay framework.
- No additional live read solely to discover revision identity.
- No private HTTPX methods or monkey-patching of HTTPX internals.

## Marker and opt-in contract

Pytest registers `unit`, `contract`, `mcp`, `live_onshape`, `live_readonly`,
`live_mutation`, `slow`, and `live_budget(N)`.

A live test can reach its setup only when all applicable conditions hold:

- The test is positively selected with a marker expression containing
  `live_onshape`.
- `JARVIS_LIVE_TESTS=1`.
- Exactly one complete credential pair is configured:
  `ONSHAPE_ACCESS_KEY` plus `ONSHAPE_SECRET_KEY`, or
  `ONSHAPE_API_KEY` plus `ONSHAPE_API_SECRET`.
- `JARVIS_LIVE_DOCUMENT_ID`, `JARVIS_LIVE_WORKSPACE_ID`, and
  `JARVIS_LIVE_ELEMENT_ID` are present and valid single URL segments.
- A live request budget is active.
- Live execution is serial.

Live mutation tests additionally require `JARVIS_LIVE_MUTATIONS=1`. A selected
live test without the global live flag skips before setup. Once the global flag
is explicitly enabled, incomplete or ambiguous credentials and missing or
invalid sandbox IDs are configuration errors that refuse execution before
client construction. Mutation tests skip when only read-only live access was
enabled.

## Physical-send budget

The supported HTTPX custom-transport interface is the accounting boundary.
`BudgetedAsyncTransport` wraps an injected `httpx.AsyncBaseTransport` and
reserves one unit immediately before delegating `handle_async_request`.

- The local live fixture injects `httpx.AsyncHTTPTransport(retries=0)`.
- Deterministic tests may inject `MockTransport`, counting transports, and
  failing transports.
- A redirect reaches the wrapper again and consumes another reservation.
- A transport exception retains the reservation because the physical attempt
  began.
- An explicit caller-level retry reaches the wrapper again and consumes
  another reservation.
- The overflowing attempt raises `LIVE_API_BUDGET_EXCEEDED` before the wrapped
  transport is invoked.
- Hidden transport retries are disabled for the real live fixture.

The defaults are three requests for a read-only test, eight for a mutation
test, and thirty for the entire live session. `live_budget(N)` may lower, but
never raise, the category default. `JARVIS_LIVE_SUITE_BUDGET` may configure a
positive suite limit and defaults to thirty. One lock protects the suite count
and every per-test count so concurrent reservations cannot overshoot.

## Offline network policy

Normal pytest execution blocks outbound Python socket operations and DNS with
`NETWORK_ACCESS_FORBIDDEN_IN_TEST`. Enabling live tests does not disable this
process-wide policy.

The test harness owns a module-private capability object stored in a
`ContextVar`. The capability is a scoping mechanism, not a cryptographic
security boundary. Only the live fixture passes the private permit context
manager to `BudgetedAsyncTransport`. The wrapper enters the permit immediately
around its injected transport delegate and resets it in `finally`. Direct
HTTPX, socket, and DNS calls remain blocked, including while the live
environment is fully configured.

If this scoped arrangement cannot work with the current HTTPX stack without
broadly enabling sockets or replacing HTTPX with a materially invasive custom
transport, implementation stops and reports the conflict.

## Telemetry and diagnostics

Live-budget telemetry contains only:

- sanitized test identifier;
- HTTP method;
- hostname;
- sanitized route template;
- per-test and suite used/limit counts;
- whether the test or suite boundary blocked the request.

Route sanitization replaces document, workspace, element, feature, part,
translation, external-data, and unknown dynamic segments with named
placeholders or `{opaque}`. Query strings and fragments are discarded.

Telemetry never stores or prints authorization headers, credentials, query
values, request bodies, response bodies, raw request/response objects, or real
private CAD identifiers.

Application diagnostics remain useful. The Onshape client may log method,
host, sanitized route template, HTTP status, reason phrase, content length,
and error class. It must stop logging complete URLs, query dictionaries,
request bodies, and response/error bodies. HTTP response objects remain
available to Jarvis error-handling code; this change sanitizes logging rather
than destroying programmatic diagnostics.

## Public fixtures and L2 replay

Public fixtures live below `tests/fixtures/` and carry provenance metadata:
source, purpose, feature count, and privacy status. Initial fixtures cover a
small clean Part Studio and unresolved geometry references. They are synthetic
and contain poison canaries proving that normal observations do not leak raw
feature documents. `tests/.local-fixtures/` is gitignored and never required by
CI.

The L2 replay route is:

```text
MCP call
-> server handler
-> governance context
-> PartStudioManager
-> OnshapeClient
-> BudgetedAsyncTransport(MockTransport)
-> normalization
-> ObservationEnvelope
```

One authoritative fixture read must support compact-tree, search, inspection,
dependency-slice, and repeated-inspection operations without another transport
invocation. Tests cover bounded output, raw leakage, dependency honesty,
hypothesis gating, ledger counts, and revision-present/revision-absent cases.

## Local live read-only acceptance

`LIVE-DEEP-READ-01` is marked `live_onshape`, `live_readonly`, and
`live_budget(1)`. It uses only environment-provided sandbox identifiers and
fixed sanitized assertion messages.

It performs one authoritative model read followed by cached compact-tree,
feature-search, feature-inspection, dependency-slice, and repeated-inspection
operations. It validates useful normalized CAD status and parameters, bounded
observations, no complete raw feature document, and exactly one physical HTTP
send.

If the authoritative response already includes revision identity, the test
requires correct propagation through working state and observations. If it does
not, explicit `None`/unknown is accepted. The test never spends another live
request to discover revision identity.

The scenario is implemented and collected by CI/local offline verification,
but WP-003 execution never runs it.

## CI and completion

Pull-request CI remains credential-free. It has blocking Ruff, offline pytest
with `-m "not live_onshape"`, global coverage of at least 80%, and focused mypy
for the new request-guard module. Coverage is removed from default pytest
addopts so focused development commands remain fast, and duplicate coverage
configuration/runs are removed.

The completion report states exact commands and results, confirms whether live
tests ran, gives live request counts when applicable, and lists known gaps. No
completion claim is made without fresh offline verification.
