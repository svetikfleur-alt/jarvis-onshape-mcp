# A Mutation Truth V5 Revision Matrix

Authorized base: `cc2b8794565e1d6af37da248995c66b43baef0b0`

Graphify reconnaissance was attempted first. The assigned worktree has no
`graphify-out/graph.json` or `.graphify_python`, and the installed `graphify`
launcher failed with `uv trampoline failed to canonicalize script path`.
Per the packet, Graphify was not repaired or rebuilt; the single fallback was
targeted `rg` navigation followed by verification in current source and tests.

| claim | current path | current behavior | status | evidence | required delta | focused validation | live layer not verified |
|---|---|---|---|---|---|---|---|
| #6 wrong `parameterId` / `assignVariable` no-op | `update_feature` -> `update_feature_params_and_check` | The current feature definition is read before POST, parameters are indexed by their actual `parameterId`, and unknown IDs raise before delegated write. The explicit LENGTH `assignVariable` / `parameterId="value"` regression is not pinned. | `PARTIAL_RESIDUAL_GAP` | `onshape_mcp/api/feature_apply.py:668-720`; generic unknown-ID coverage in `tests/api/test_feature_apply.py:237-249` | Add the exact LENGTH `assignVariable` regression fixture and prove POST is not called. Do not add a global slot allowlist. | New issue-#6 regression test plus the focused feature-apply suite. | No live Onshape verification; prohibited by packet. |
| generic semantic state classification | `FeatureApplyResult`, `public_dict`, `_feature_apply_json`, `_exception_json`, `apply_assembly_feature_and_check` | The exact four-state vocabulary exists and Part Studio helpers use it, but public projection trusts a caller-supplied `ok=True`; assembly apply labels response-only regeneration evidence `verified`; definitive HTTP/local rejections are serialized as `unverified`. | `CONFIRMED_GAP` | `onshape_mcp/api/feature_apply.py:28-84,530-619`; `onshape_mcp/server.py:2922-3059`; existing server test fixture constructs `ok=True` with default `unverified` at `tests/test_server_handler_coverage.py:37-54` | Fail closed at the public result boundary, keep response-only assembly success `unverified`, and classify only definitive pre-write/HTTP rejection as `failed`; retain ambiguous transport/post-write failures as `unverified`. | RED/GREEN tests for inconsistent result projection, assembly response-only success, HTTP rejection, local validation rejection, and ambiguous request errors. | No live Onshape verification; prohibited by packet. |
| create path truth | Part Studio create handlers -> `apply_feature_and_check` | POST success is followed by authoritative `/features` reread; feature presence, regeneration state, and comparable requested fields are required for `verified`. Missing ID/status, reread failure, or ambiguous canonicalization cannot return success. An empty or identity-only requested feature was nevertheless treated as a vacuous match. | `PARTIAL_RESIDUAL_GAP` | `onshape_mcp/api/feature_apply.py:284-527`; `tests/api/test_feature_apply.py:293-468`; create handlers route through the helper in `onshape_mcp/server.py`. | Require at least one substantive requested feature field before comparison can yield `verified`. | Existing create truth tests, a vacuous-request regression, and public server mutation tests. | No live CAD regeneration/geometry/UI evidence; prohibited by packet. |
| update path truth | `update_feature_params_and_check` -> `apply_feature_and_check(operation="update")` | Current feature data is fetched, requested parameter IDs are validated before POST, already-satisfied requests return `no_effect`, and the authoritative reread yields `verified`, `no_effect`, `failed`, or `unverified` from requested fields. | `ALREADY_FIXED_IN_BASE` | `onshape_mcp/api/feature_apply.py:622-848`; `tests/api/test_feature_apply.py:116-290,469-599`; `onshape_mcp/server.py:4124-4139` | Preserve behavior; add only the explicit issue-#6 regression in row 1. | Existing update truth tests plus the new issue-#6 test. | No live Onshape verification; prohibited by packet. |
| delete path truth | `delete_partstudio_feature_and_check` | Pre-state is read, already-absent is `no_effect`, successful transport is reread, retained target is `no_effect`, missing regeneration evidence is `unverified`, new regeneration errors are `failed`, and only verified absence with healthy post-state is `verified`. | `ALREADY_FIXED_IN_BASE` | `onshape_mcp/api/feature_apply.py:851-1005`; `tests/api/test_feature_apply.py:600-644`; server routing at `onshape_mcp/server.py:4036-4117` | None. | Existing delete helper and public handler tests. | No live deletion/regeneration evidence; prohibited by packet. |
| sketch/edit path truth where generic machinery applies | `edit_sketch` -> `apply_feature_and_check(..., verify_requested_state=False)` -> sketch-specific comparator | The generic write/reread result is refined by authoritative sketch membership and safe field comparison. Missing reread is `unverified`, stable mismatch is `failed`, and verified additions/removals are `verified`. | `ALREADY_FIXED_IN_BASE` | `onshape_mcp/api/sketch_edit.py:483-602`; `tests/api/test_feature_apply.py:826-1156`; public shaping at `onshape_mcp/server.py:5046-5076` | None. | Existing sketch mutation truth and public handler tests. | No live sketch solver/viewport evidence; prohibited by packet. |
| bounded sanitized mutation diagnostics | `safe_http_diagnostic`, `_exception_json`, `FeatureApplyResult.public_dict` | Transport, HTTP, regeneration, reread, and semantic fields remain distinct. Raw response evidence is excluded, routes/identifiers are redacted, structured categories/reference paths are bounded, and arbitrary response bodies do not cross the MCP boundary. | `ALREADY_FIXED_IN_BASE` | `onshape_mcp/api/feature_apply.py:57-84`; `onshape_mcp/server.py:2922-3059`; `tests/api/test_feature_apply.py:646-717`; `tests/api/test_http_diagnostics.py` | Preserve the bounded diagnostic projection while correcting the semantic state selected for definitive rejection. | Existing diagnostic/privacy suites plus classification regressions from row 2. | No live HTTP payloads or credentials used; prohibited by packet. |

## Pre-edit baseline

- `uv run --frozen pytest -m "not live_onshape" --collect-only -q`:
  1,055 selected, 1 deselected.
- `uv run --frozen pytest -q --maxfail=1 tests/api/test_feature_apply.py tests/api/test_http_diagnostics.py tests/test_server_handler_coverage.py`:
  108 passed.
- `uv run --frozen pytest -q --maxfail=1 tests/test_server.py` with a
  worktree-local temporary root because the host temp root was inaccessible:
  155 passed.
- `git diff --check`: clean.

## Implemented outcome

- Added the exact issue-#6 LENGTH `assignVariable` regression. It passed
  before production edits, proving the base already rejected
  `parameterId="value"` from the current feature definition before POST.
- Public mutation projection now requires transport, HTTP, regeneration, and
  `mutation_verification="verified"` evidence before emitting `ok=true`.
- Part Studio create cannot classify an empty or identity-only requested
  feature as verified.
- Assembly OK/INFO response status remains regeneration evidence but is
  `unverified` without an authoritative requested-state reread; ERROR is
  `failed`, and missing status is `unverified`. No new assembly GET was
  introduced.
- HTTP 4xx and explicitly identified pre-write rejection classify as
  `failed`; HTTP 5xx, transport failure, and other ambiguous exceptions
  remain `unverified`.
- Update preflight failures use a dedicated exception type. A POST response
  parsing failure is therefore still `unverified` with unknown effect rather
  than being mislabeled as a pre-write rejection.
- Consolidated focused validation: 275 passed. Focused Ruff validation passed.

## Final offline validation

- `uv run --frozen ruff check onshape_mcp tests`: passed.
- `uv run --frozen pytest -m "not live_onshape" -q --maxfail=1`:
  1,067 passed, 1 deselected.
- `uv run --frozen pytest -m "not live_onshape" --cov=onshape_mcp --cov-report=term-missing`:
  1,067 passed, 1 deselected; total coverage 87.87%.
- The first full-test attempt used a worktree-local temp root and caused one
  nested WP003 harness test to inherit repository marker configuration. The
  identical command passed when rerun with a dedicated external temp root;
  no source change was made for that environment-only failure.

## Scope ruling

The residual work is a bounded correction to the existing result model,
serializer, assembly-side generic apply helper, and focused tests. It does not
require a new mutation architecture, builder-schema work, live Onshape access,
or a full-model verification loop.
