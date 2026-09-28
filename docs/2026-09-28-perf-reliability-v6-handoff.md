# Full handoff package — Jarvis V6 performance, mutation truth, and state hardening

## Handoff status

- Terminal state: `BLOCKED`
- Blocker: the focused live validation could not run because neither supported
  Onshape credential pair nor a repository `.env` was available.
- Live Onshape HTTP calls: `0`
- Live transmitted mutations: `0`
- Remote pushes, merges, PR changes, or deployments: none
- Previous enclosure benchmark document: untouched
- Primary checkout: untouched
- Assigned worktree was clean before implementation and after the two
  implementation/documentation commits.

The implementation is offline-complete and committed. Do not claim live
Onshape, geometry, topology, render, UI, or benchmark acceptance until the
guarded disposable-document scenario below passes.

## Repository identity

- Repository: `svetikfleur-alt/jarvis-onshape-mcp`
- Worktree: `D:\jarvis-onshape-mcp\.worktrees\perf-reliability-v6`
- Branch: `codex/perf-reliability-v6`
- Required base: `7d3ed52962721f9385a054d92a58d4ad34b66485`
- Implementation commit: `0eb341fe346aa7a32a6af3c53070cfc0228444e7`
- Protocol/evidence commit: `e4cb3da42831964a987d0d0716f50a2d5f129bca`
- Do not push, merge, rebase, reset, clean, or modify the dirty primary checkout
  without new authorization.

## Accepted scope

The completed slice covers:

1. canonical mutation verification for Extrude create/update, linear feature
   Pattern, Shell, Chamfer, Draft, and Move Body;
2. targeted feature rereads instead of full-tree mutation verification reads;
3. mutation-aware feature context reconciliation and topology invalidation;
4. one bounded authoritative refresh for stale/missing sketch face evidence;
5. topology snapshot reuse until invalidated;
6. bounded Move Body/change-result serialization;
7. baseline-aware document hygiene;
8. explicit sketch-constraint quality uncertainty;
9. HTTP/cache/MCP invocation, round-trip, elapsed-time, and payload-size metrics;
10. removal of agent instructions that required verification-only follow-up
    describes after already-verified mutations;
11. a guarded live validation for one newly-created disposable document.

The addendum did not authorize a new agent protocol or compound-tool
architecture. None was added.

## Source provenance

- Current source and tests are authoritative for implemented behavior.
- The first enclosure benchmark figures are historical evidence supplied by
  the work packet, not measurements reproduced on this branch.
- Official Onshape Features API and FeatureScript sketch documentation were
  used only to confirm supported feature filtering and documented sketch
  constraint surfaces.
- No live provider behavior was inferred from offline mocks.
- Graphify output was absent in this worktree, so source navigation was bounded
  with direct searches and reads. Graphify was not repaired or regenerated.

## Proven root causes

### False failed/unverified mutation classifications

The shared comparator treated Onshape wire metadata, BT type version suffixes,
reference ordering, and quantity expression spelling as semantic. Updates also
accepted equal numeric values too broadly, including cases where the requested
literal would replace a variable binding. Empty `deterministicIds` arrays could
incorrectly suppress meaningful query-text differences.

The repaired comparator:

- normalizes BT type version suffixes;
- ignores only established nonsemantic metadata;
- compares nonempty deterministic/feature reference sets without ordering;
- keeps empty-ID query text unverified unless another authoritative reference
  proves equivalence;
- compares concrete quantities by compatible dimensional kind and SI value;
- never equates a variable expression with an equal-valued literal;
- continues checking every requested field after one canonical match.

### Read amplification

Create verification used an unfiltered feature-tree GET. Existing-feature
update used a full-tree GET before and after mutation. Server and skill
instructions also told the model to run `describe_part_studio` after every
visible mutation, including verified results.

Create now uses one targeted `featureId` reread. Update uses one targeted
preflight read and one targeted verification read. Verified mutation hints and
the Onshape skill now permit direct continuation unless topology, dimensions,
rendering, or another engineering judgment is actually needed.

`trackChanges=True` remains an explicit diagnostic cost: it performs before and
after bodydetails and mass-property reads. This is a known remaining cost, not
part of semantic verification.

### Stale topology and preflight rejection

Topology enumeration previously had no shared freshness state or mutation
invalidation. Sketch face resolution trusted the supplied ID without a bounded
authoritative recovery path.

EntityManager now caches bodydetails/face frames per Part Studio, invalidates
them after Part Studio mutations, and validates sketch face references. A
missing or invalidated cache receives at most one authoritative bodydetails
refresh. Fresh ambiguity fails closed. Variable Studio mutations conservatively
invalidate every known Part Studio context/topology snapshot in the same
workspace.

The benchmark's automatic approval rejection happened before MCP dispatch.
That external gate is not implemented in this repository. Jarvis now provides
fresh internal preflight once a call reaches it, and the agent procedure requires
fresh entity evidence before picked-geometry decisions; the external approval
integration itself remains `NOT VERIFIED`.

### Move Body payload inflation

Face/edge identity included coordinates, so a translation made stable topology
look removed and re-added. The MCP adapter then serialized every item.

Deterministic IDs plus surface/curve type now preserve translated identity.
Large public change arrays become bounded counts and eight-item samples with
explicit truncation. Exact arrays remain internal for diagnostics.

### Hygiene baseline error

Document creation retained no complete bootstrap element baseline. The new
session-local tracker records all initial elements, intentional additions,
temporary Jarvis additions, and unknown additions separately. Default Assembly
and BOM elements captured at bootstrap do not count as Jarvis clutter. No
destructive deletion was added.

### Sketch underconstraint

Jarvis already supports geometric and dimensional constraints through
`create_sketch` and `edit_sketch`. Coordinate-first helpers can still create
valid profiles without a complete engineering constraint system. Current
authoritative feature responses expose entities, constraints, and regeneration
state but no documented fully-constrained/DOF result. Inspection now returns a
compact `UNVERIFIED` constraint-quality projection rather than claiming
production completeness.

## Implementation map

- `onshape_mcp/api/feature_apply.py`
  - canonical requested-state comparison;
  - targeted create/update reads;
  - honest mismatch/no-effect/unverified handling;
  - optional compact invalidation projection.
- `onshape_mcp/api/entities.py`
  - scoped topology cache;
  - per-Part-Studio and per-workspace invalidation;
  - one-refresh reference validation.
- `onshape_mcp/governance/context.py`
  - authoritative feature deltas;
  - pure-create reuse;
  - partial update state that cannot be served as current;
  - complete post-delete replacement;
  - workspace invalidation and topology freshness metadata.
- `onshape_mcp/api/geometry_diff.py`
  - stable translated topology identity;
  - bounding-box movement summary.
- `onshape_mcp/governance/metrics.py`
  - bounded counters and recent tool events;
  - attempted/completed/failed HTTP accounting;
  - mutation/broad/targeted/topology/cache/payload metrics;
  - no arguments, IDs, credentials, or CAD payload retention.
- `onshape_mcp/governance/hygiene.py`
  - baseline and addition provenance;
  - `CLEAN`, `NOT CLEAN`, and unavailable-baseline result.
- `onshape_mcp/server.py`
  - mutation/cache synchronization wrappers;
  - compact change results;
  - face preflight refresh;
  - `get_execution_metrics` and `get_document_hygiene`;
  - MCP boundary invocation/response-size timing;
  - corrected verified/failed mutation hints.
- `onshape_mcp/api/sketch_inspect.py`
  - explicit constraint-quality uncertainty.
- `skills/onshape/SKILL.md`
  - visual checkpoints instead of unconditional verification follow-ups.
- `tests/live/test_live_optimization_validation.py`
  - disposable document scenario bounded below the packet ceilings.
- `docs/2026-09-28-perf-reliability-v6-evidence.md`
  - detailed call paths, classification evidence, and sketch follow-up packet.

## Mutation truth contract

Offline canonical paths for all required capabilities return `verified`:

| Capability | Covered requested-state evidence |
|---|---|
| Extrude create | feature/type, source sketch reference, enums/booleans, canonical depth |
| Extrude update | requested parameter fields, new value, binding semantics |
| Linear feature Pattern | source feature set, direction reference, count, spacing |
| Shell | removed-face set, thickness, direction |
| Chamfer | edge set, type, width |
| Draft | neutral plane, drafted-face set, angle, direction |
| Move Body | body set and canonical translation fields |

HTTP success alone never verifies. Regeneration errors, missing authoritative
status, missing feature identity, retained old state, incompatible quantity
kinds, variable/literal binding differences, and stable requested-state
mismatches remain non-verified.

## Cache and invalidation contract

- A successful create may append its targeted authoritative feature while
  preserving established upstream features.
- Updating an existing feature creates a `partial` wider context because
  downstream evaluated state may have changed. Feature-tree/dependency reads
  fail closed rather than serving that partial tree as current.
- A complete post-delete snapshot replaces the cached tree.
- Part Studio mutations evict only the matching topology snapshot.
- Variable Studio mutation invalidates all known Part Studio contexts and
  topology snapshots in the same document/workspace because dependencies are
  not yet mapped authoritatively.
- A fresh topology read marks matching context topology evidence fresh.
- Stable document/workspace/element IDs remain in the context model reference.

## Performance evidence

Structural offline evidence:

- create verification: one POST + one targeted feature GET;
- update verification: targeted GET + POST + targeted GET;
- mutation verification broad feature refreshes: zero;
- repeated topology calls: first miss followed by cache hits until mutation;
- Move Body regression fixture: 27,409-byte pre-fix output versus a post-fix
  bound below 5,000 bytes;
- HTTP attempts, completions, failures, request/response bytes, cache events,
  and MCP response families are now measurable.

Do not extrapolate these values to the full enclosure benchmark. The exact same
benchmark must be rerun after focused live acceptance.

## Agent execution overhead evidence

The original aggregate accounts for at least 63 MCP/tool invocations:

- 25 mutation requests;
- 12 targeted feature inspections;
- 13 filtered topology reads;
- 11 full feature refreshes;
- 2 broad descriptions.

The exact invocation total and model/tool round-trip count are `NOT VERIFIED`
because the original run retained aggregate counts rather than a per-call trace.

Supported classifications:

| Interaction | Classification |
|---|---|
| Distinct requested CAD mutations | `ENGINEERING_DECISION_REQUIRED` |
| Later reads settling 11 disputed successful mutations | `VERIFICATION_FOLLOWUP` |
| Full refresh used solely to repeat mutation verification | `REDUNDANT_READ` |
| Three rejected Sketch attempts plus topology rereads | `STALE_STATE_RECOVERY` |
| Former Move Body topology result | `LARGE_RESPONSE_OVERHEAD` |
| POST + targeted reread + canonical compare + state reconciliation | `DETERMINISTIC_INTERNAL_WORK` |
| Selecting a new face/edge based on geometry | `ENGINEERING_DECISION_REQUIRED` |

Deterministic work already internalized under this packet:

- mutation semantic reread;
- canonical comparison;
- feature-context reconciliation;
- topology invalidation;
- one bounded stale-reference refresh.

Candidates for a later Execution Protocol Optimization packet:

- mutation schemas that accept an existing context handle for target reuse;
- logical-operation correlation IDs spanning model and tool events;
- compact topology-selection projections;
- explicit model-decision versus deterministic-internal-work annotations.

Do not compound operations when geometry selection or design intent requires a
model decision.

## Validation evidence

- Focused mutation/cache/capability/server/harness selection: `307 passed`.
- Full offline suite: `1199 passed, 3 live tests deselected`.
- Ruff CI scope: `ruff check onshape_mcp tests` passed.
- `git diff --check` passed.
- Final offline warnings: three pre-existing Pydantic v2 class-config
  deprecation warnings in `onshape_mcp/api/documents.py`.
- Read-only adversarial review: complete. Nine correctness gaps were reproduced
  and repaired, covering multi-field update comparison, literal/variable
  binding, dimensional kinds, empty query IDs, failed-result hints, stale and
  partial contexts, feature deletion, Variable Studio invalidation, and failed
  HTTP/MCP accounting.
- Revised Onshape skill pressure review: verified conceptually by the permitted
  read-only reviewer. A verified mutation skips a verification-only describe;
  topology/render evidence remains required for genuine engineering decisions
  and final QA.
- Live validation: not run; credentials unavailable.
- Final worktree after implementation commits: clean.

## Guarded live acceptance

Prerequisites:

- exactly one complete pair:
  - `ONSHAPE_ACCESS_KEY` + `ONSHAPE_SECRET_KEY`, or
  - `ONSHAPE_API_KEY` + `ONSHAPE_API_SECRET`;
- `JARVIS_LIVE_TESTS=1`;
- `JARVIS_LIVE_MUTATIONS=1`;
- `JARVIS_LIVE_SUITE_BUDGET=60`;
- no `JARVIS_LIVE_DOCUMENT_ID`, `JARVIS_LIVE_WORKSPACE_ID`, or
  `JARVIS_LIVE_ELEMENT_ID` is required or used.

PowerShell command after the credential pair is present in the process
environment:

```powershell
$env:JARVIS_LIVE_TESTS = '1'
$env:JARVIS_LIVE_MUTATIONS = '1'
$env:JARVIS_LIVE_SUITE_BUDGET = '60'
& 'D:\jarvis-onshape-mcp\.venv\Scripts\python.exe' -m pytest `
  tests\live\test_live_optimization_validation.py `
  -m 'live_onshape and live_optimization_validation' `
  -q --maxfail=1
```

The scenario:

- creates one new disposable document;
- creates Sketch, Extrude, linear feature Pattern, face-reference Sketch,
  Shell, Chamfer, and one existing-feature update;
- exercises stale-topology preflight recovery and baseline-aware hygiene;
- asserts eight mutation HTTP calls, zero broad feature reads, bounded topology
  reads, correct first-pass semantic classifications, and execution metrics;
- uses a 40-HTTP per-test cap under the packet's 60-HTTP ceiling;
- never targets a pre-existing document;
- does not intentionally manufacture transport failures;
- does not delete the validation document.

If the test fails, preserve its telemetry and do not repeat a mutation blindly.
Use the reason code and last authoritative evidence to identify the failed
layer. Maximum two bounded recovery attempts per operation still applies.

## Acceptance after the live run

Only after the focused live test passes:

1. record physical HTTP calls, mutation calls, tool invocations, topology reads,
   targeted/broad reads, cache events, response-family sizes, and first-pass
   mutation classifications;
2. confirm the document baseline excludes default Assembly/BOM from Jarvis
   clutter;
3. confirm every final feature regenerates successfully;
4. confirm no manual model-driven refresh was needed solely to prove a mutation;
5. inspect final Git status and keep the worktree clean;
6. mark the branch ready for integration review;
7. rerun the exact same enclosure benchmark later for the real before/after
   comparison.

## Known limits and risks

- Actual provider behavior for targeted `featureId` rereads is not live-proven
  on this branch.
- External clients that depended on unbounded large `changes` arrays are not
  live-tested. Internal exact evidence remains available; large MCP arrays are
  intentionally sampled.
- `trackChanges=True` retains four extra diagnostic reads.
- Automatic approval rejection before MCP dispatch is outside this repository.
- Sketch fully-constrained/DOF truth remains unavailable from the inspected
  documented interfaces.
- Hygiene `CLEAN` means no tracked temporary Jarvis leftovers; unknown additions
  remain unknown and are never relabeled or deleted.
- Metrics approximate model/tool round trips from MCP invocations; they do not
  measure hidden model deliberation.

## Sketch Constraint Engineering follow-up

The separate bounded packet should:

1. establish with official schema evidence and a disposable live probe whether
   a supported endpoint returns solver DOF or fully-constrained status;
2. expose a compact authoritative projection if available;
3. otherwise implement only a conservative missing-dimension/anchor analyzer,
   never a false full-constraint claim;
4. cover fully constrained, underconstrained, conflicting, construction, and
   externally referenced sketches;
5. exclude automatic constraint insertion and a sketch subsystem rewrite
   unless separately approved.

Invariant: profile existence plus regeneration does not prove production-sketch
completeness.

## Resume brief for the next agent

```text
Continue the Jarvis V6 Performance, Mutation Truth & State Hardening packet in
D:\jarvis-onshape-mcp\.worktrees\perf-reliability-v6 on branch
codex/perf-reliability-v6. Verify the worktree is clean and HEAD contains
0eb341fe346aa7a32a6af3c53070cfc0228444e7 followed by
e4cb3da42831964a987d0d0716f50a2d5f129bca. Read AGENTS.md, the original work
packet/addendum, docs/2026-09-28-perf-reliability-v6-evidence.md, and this
handoff. Do not modify the dirty primary checkout, push, merge, or touch a
pre-existing Onshape document. Configure exactly one supported credential pair
without printing it, then run only the guarded disposable optimization live
test with JARVIS_LIVE_TESTS=1, JARVIS_LIVE_MUTATIONS=1, and suite budget 60.
Preserve telemetry. If it passes, report the exact live counts and prepare the
branch for integration review and the later identical enclosure benchmark. If
it fails, repair only the proven layer within the packet's repair cap and rerun
the focused/offline checks before another bounded live attempt.
```

## Final state

`BLOCKED: focused live validation requires one supported Onshape credential pair; no live calls have been attempted.`
