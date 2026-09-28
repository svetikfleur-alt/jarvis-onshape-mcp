# Execution Protocol Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a preferred single-call verified feature path, compact model/feature reads, bounded response shaping, and minimal protocol measurements without changing mutation truth or cache behavior.

**Architecture:** A new adapter module shapes results and state at the MCP boundary. `server.py` only registers and dispatches the preferred tools, delegates mutations to the existing handlers/verifiers, and wraps the outer MCP boundary for metrics.

**Tech Stack:** Python 3.10+, MCP Python SDK, Pydantic result models, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/execution-protocol-v6-design.md`

## Global Constraints

- Work only in `D:/jarvis-onshape-mcp/.worktrees/execution-protocol-v6` on `codex/execution-protocol-v6` from base `7d3ed52962721f9385a054d92a58d4ad34b66485`.
- Do not alter WorkingState, cache semantics, invalidation policy, mutation-truth comparison, or transport behavior.
- Delegate exactly one requested mutation and never embed LLM/planning behavior.
- Preserve all existing low-level tools; compact mode is the preferred adapter and diagnostic mode retains their existing bounded result.
- No live Onshape call before the offline suite and Ruff are green; live work is limited to one new disposable document, 50 HTTP calls, and 10 mutations.

## Review Focus

- A malformed or unsupported operation must fail before any delegated mutation.
- A delegated failure/unverified result must retain blocker, reason code, and uncertainty.
- Compact output must not contain face/edge arrays or raw feature trees, even with pathological input.
- Diagnostic output must retain the existing bounded low-level evidence without exposing internal `FeatureApplyResult.raw`.
- Metrics must count one outer `execute_feature` invocation, not the internal delegated handler as a second MCP call.

---

### Task 1: Compact result and state foundation

**Files:**
- Create: `onshape_mcp/execution_protocol.py`
- Create: `tests/test_execution_protocol.py`
- Modify: `docs/execution-protocol-v6-design.md`

**Interfaces:**
- Consumes: `FeatureApplyResult.public_dict()` and normalized Onshape feature/parts documents.
- Produces: `canonical_request(operation, request) -> dict`, `compact_execution_result(...) -> dict`, `compact_feature(...) -> dict`, `compact_model_state(...) -> dict`, and `ExecutionProtocolMetrics`.

- [ ] **Step 1: Write failing compact-result tests**

Add literal assertions for verified, failed, and unverified mutation states; bounded `change_summary` counts; structural absence of face/edge arrays; and diagnostic retention of an existing bounded legacy result.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run --frozen --extra dev pytest tests/test_execution_protocol.py -q --basetemp .pytest-tmp`

Expected: collection/import failure because `onshape_mcp.execution_protocol` does not exist.

- [ ] **Step 3: Implement minimal pure adapters**

Implement allowlisted operation parameters, target projection, compact mutation state, bounded change summary, normalized parameter summaries, recent feature rows, part summaries, failure rows, explicit `unknown_or_stale`, and lock-protected process-local metrics. No network or mutation code belongs in this module.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 focused command; expected all tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add compact execution protocol foundation`

### Task 2: Preferred MCP compound operations

**Files:**
- Modify: `onshape_mcp/server.py`
- Modify: `tests/test_execution_protocol.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_server_handler_coverage.py`

**Interfaces:**
- Consumes: Task 1 adapters and current low-level handler results.
- Produces: MCP tools `execute_feature`, `inspect_feature_compact`, `get_compact_model_state`, and `get_execution_protocol_metrics`; `_call_tool_impl` remains the sole low-level dispatch implementation and `call_tool` is the measured outer boundary.

- [ ] **Step 1: Write failing schema and dispatch tests**

Assert the four tools are listed; `execute_feature` delegates once to the selected existing handler, returns the same feature ID and requested canonical value, rejects nested/unsupported execution, preserves failed/unverified blockers, and diagnostic mode retains the legacy result. Assert compact inspection performs one feature read and compact state performs only feature/parts reads.

- [ ] **Step 2: Run focused MCP tests and verify RED**

Run the focused execution-protocol, server schema/dispatch, and handler-coverage test files. Expected failures name missing tools/dispatch branches.

- [ ] **Step 3: Wire the minimal adapter boundary**

Register schemas; split outer `call_tool` from `_call_tool_impl`; reject `execute_feature` recursion and unsupported names; delegate exactly once; shape compact/diagnostic output; concurrently read features/parts for compact state; and expose the metrics snapshot.

- [ ] **Step 4: Run focused MCP tests and verify GREEN**

Run the Task 2 focused command; expected all tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add preferred verified MCP execution path`

### Task 3: Agent guidance, response-size guardrails, and measurement evidence

**Files:**
- Modify: `onshape_mcp/server.py`
- Modify: `skills/onshape/SKILL.md`
- Modify: `README.md`
- Modify: `tests/test_execution_protocol.py`

**Interfaces:**
- Consumes: Task 2 preferred tool surface.
- Produces: instructions that select the compact verified path by default, explicit advanced/raw guidance, stable payload upper-bound/structural tests, and metric-family evidence.

- [ ] **Step 1: Write failing behavior tests for response bounds and metrics**

Assert pathological topology-shaped legacy changes produce a compact response below a sensible non-byte-perfect ceiling, raw evidence appears only in diagnostic mode, one outer call increments one MCP invocation/logical operation, and response family maxima are recorded.

- [ ] **Step 2: Run focused tests and verify RED**

Run the Task 1 focused command; expected failures identify missing bounds/measurement behavior.

- [ ] **Step 3: Update guidance and finish measurement hooks**

Replace unconditional post-mutation describe guidance with: trust `verified` requested-state truth for ordinary continuation; request topology/render only when the next engineering decision needs it. Document preferred vs advanced tools and the meaning of metric proxies.

- [ ] **Step 4: Run focused and compatibility tests**

Run execution-protocol tests, schema/dispatch tests, mutation response tests, handler coverage, relevant server tests, Ruff, and `git diff --check`; expected all pass.

- [ ] **Step 5: Commit**

Commit message: `docs: prefer compact verified execution protocol`

### Task 4: Final regression, review, and focused live validation

**Files:**
- Modify only if a RED regression or Important/Critical review finding requires a test-first fix.

**Interfaces:**
- Consumes: complete branch diff and the explicit live gate.
- Produces: exact offline counts, one read-only adversarial review, disposable-document live counts, and clean committed worktree.

- [ ] **Step 1: Run final offline validation**

Run the full `not live_onshape` suite with worktree-contained temp storage, Ruff, and `git diff --check`.

- [ ] **Step 2: Run one read-only adversarial reviewer**

Review round trips, hidden decisions, compaction correctness, verification preservation, and merge risk with `codex/perf-reliability-v6`. Fix only Critical/Important findings via RED/GREEN tests.

- [ ] **Step 3: Run focused live validation**

Create one new disposable document and execute Sketch, Extrude create, same-Extrude update, and one Pattern or Chamfer through the preferred path. Do not touch existing CAD. Stop at 50 HTTP calls or 10 mutations.

- [ ] **Step 4: Record evidence and commit any final test-first fixes**

Report exact tests, metrics, live calls/mutations, unverified layers, commit SHAs, and clean worktree status.
