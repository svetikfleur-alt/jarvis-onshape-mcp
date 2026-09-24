# Jarvis Agent Rules

Scope: repository-specific overlay for `svetikfleur-alt/jarvis-onshape-mcp`.
The global Agent Operating Standard still applies. These rules specialize Jarvis behavior within this repository.

## Repository identity and map

- Primary project: `svetikfleur-alt/jarvis-onshape-mcp`.
- Upstream reference: `ReshefElisha/jarvis-onshape-mcp`.
- Confirm repository, branch/worktree, revision, and active work packet before edits. Stop if the checkout or architectural boundary does not match the task.
- Do not modify an upstream, sibling repository, or another worktree by inference.
- `onshape_mcp/server.py`: MCP schemas, dispatch, response shaping, entry points.
- `onshape_mcp/api/`: Onshape transport/domain API operations.
- `onshape_mcp/builders/`: deterministic feature payload builders.
- `onshape_mcp/tools/`: bounded tool helpers.
- `onshape_mcp/governance/`: context handles, budgets, working state, read ledger, bounded observations, feature intelligence.
- `onshape_mcp/analysis/`: geometry/assembly analysis helpers.
- `skills/onshape/`, `skills/vision-decompose/`: agent-facing CAD procedures.
- `tests/`: executable contracts/regression evidence.
- `README.md`, `RESEARCH.md`, `knowledge_base/`, `docs/`: supporting/reference material; verify code-driving claims against current source.
- `scripts/`: probes/smokes; never assume live safety from a script name.

## Architectural invariants

1. The CAD, context, validation, and state core must remain provider-neutral.
2. Provider SDKs/models/runtimes are clients or adapters, not dependencies of CAD truth.
3. Provider-specific behavior may remain at adapter boundaries; do not move it into core CAD/context/domain models.
4. Keep raw Onshape/API payloads server-side. Return raw material only through an approved bounded/redacted interface or diagnostic path.
5. Model context receives bounded projections, handles, summaries, pages, and deltas—not repeated giant JSON.
6. Existing-model intelligence is a primary capability, including Part Studios with 200+ features.
7. Deterministic IDs, revisions, counters, budgets, schemas, diffs, and exact state comparisons must stay deterministic where deterministic truth exists.
8. Unknown CAD semantics remain unknown until authoritative evidence resolves them.

## Project truth and navigation

1. Current source code is authoritative for what is implemented; canonical architecture documents are authoritative for accepted architectural intent.
2. If implementation and accepted intent diverge, report it; do not silently redefine either one.
3. Tests prove only the contracts they assert. Historical work packets/handoffs do not prove that a change is present in the current branch.
4. The active work packet governs the current slice unless a higher-priority instruction overrides it.
5. When Graphify/code-graph output is available, scoped, sanitized, and useful, use it to narrow traversal.
6. Graphify is navigation/index only—not source-of-truth, runtime truth, semantic proof, or memory canon.
7. If graph freshness is uncertain, use it only to locate candidates; verify edit-driving facts in current source/canonical docs.
8. Do not invent a Graphify freshness SLA.
9. Avoid broad scans when targeted search/index/known paths can answer the question. Widen only for a named unresolved question; exclude caches, environments, generated outputs, and unrelated worktrees.
10. A genuinely broad recursive scan requires explicit justification and authorization from the active task/work packet or user.

## Context governance and large models

1. Read the target and enough relevant callers/dependencies/schemas/tests to establish the affected contract and risks.
2. Reuse verified findings only while source identity/revision and the required evidence remain valid.
3. Reuse cached authoritative Onshape data when it is from the same source revision and contains the evidence required by the current question.
4. A new evidence requirement may justify a deeper read without a source revision change.
5. Repeated identical reads must be deduplicated or explicitly justified.
6. Start large-model work with a compact overview/feature index; form an explicit hypothesis or target region before deeper inspection.
7. Narrow by feature ID/type/status/name/ordinal or explicit dependency evidence. Inspect only the slice needed for the current question.
8. Bound direction, depth, nodes, edges, rows, parameters, images, and returned payloads. Report truncation/unresolved references explicitly.
9. Do not flatten the whole model into the prompt merely because the API can return it.
10. Internal numeric observation/tool limits live in governance code/tests; do not duplicate stale constants here.
11. Never leak secrets, signed request material, irrelevant model data, or unbounded raw payload subtrees into agent context.

## Dependency semantics

1. Create dependency edges only from explicit cached feature references or other authoritative evidence.
2. Do not infer dependencies from proximity, naming, feature order, geometry resemblance, or convenience.
3. Preserve direction and evidence type.
4. Missing/ambiguous geometry references or feature IDs remain unresolved; do not convert them into graph edges.
5. Distinguish `not_found`, `unsupported`, `unverified`, and `no_dependency`.

## Live Onshape gate

1. No live Onshape call without explicit authorization for the current task.
2. Read authorization does not imply mutation authorization.
3. Offline fixtures, mocks, recorded payloads, and local tests are the default.
4. Never use credentials merely because they are available; never log, echo, persist, commit, or expose credentials/signed request material.
5. Authorized live access must stay inside the named document, workspace, element, operation, and mutation scope. Stop before any live call outside it.

## Mutation pre-flight

Before an authorized mutation:

1. Confirm document/workspace/element, target feature/entity IDs, and current revision or microversion.
2. Confirm authoritative current state and the exact requested delta.
3. Validate schema, parameter IDs, enums, units, references, and operation semantics locally. If only live validation can resolve a requirement, stop unless the live gate authorizes it.
4. Reject invalid/unknown identifiers before the network.
5. Identify dependency, regeneration, geometry, and data-loss risks.
6. Define expected post-state and the evidence that can verify it.
7. Preserve a recovery/rollback path where material harm is possible.
8. Stop if a material CAD/product interpretation remains unresolved.

## Mutation post-flight and semantic success

Keep these layers distinct:

1. request/transport;
2. HTTP;
3. payload/API acceptance;
4. regeneration/notices;
5. authoritative requested-state reread or delta;
6. geometry/topology;
7. feature/dependency graph;
8. CAD semantics/design intent;
9. viewport/render;
10. UI/end-to-end;
11. regression/invariants.

Mutation states:

- `verified`: authoritative evidence matches requested state.
- `no_effect`: transport may have succeeded, but authoritative state did not change or was already satisfied.
- `failed`: comparable evidence contradicts requested state or regeneration failed.
- `unverified`: requested state cannot be proven from available authoritative evidence.

Only verified requested state may produce a semantic success claim.

## CAD validation

1. Evidence must match the claim; a lower validation layer never proves a higher layer.
2. Build/unit success does not prove real Onshape regeneration, geometry, topology, or design intent.
3. HTTP 2xx does not prove mutation.
4. Regeneration without authoritative reread does not prove requested fields.
5. A visible shape does not prove stable feature history, dependency correctness, or parametric intent.
6. A render does not prove hidden geometry, dimensions, mating, tolerance, or manufacturability.
7. After implementation/final repair is stable, run one focused validation pass on the changed contract, then one risk-appropriate final regression/invariant pass.
8. Debugging checks during repair do not replace those final passes.
9. If a final pass fails, return to repair/blocker state; do not count it as completion.
10. Live acceptance is a separate explicitly authorized layer. Report every unrun/unproven material layer.

## Tests

1. Encode intended CAD/context behavior and meaningful failures.
2. For reproducible defects, add a regression test that fails before the fix and passes after it.
3. Cover relevant boundaries: units, malformed/unknown IDs, regeneration errors, authoritative-reread failure, no-effect mutation, truncation, caching, and budget gates.
4. Do not weaken assertions to make implementation pass.
5. Mocks do not prove provider or Onshape behavior; deterministic validators precede model judgment where deterministic truth exists.

## Jarvis context budget

1. Quality is non-negotiable.
2. For a bounded implementation task, about 15–25k reconnaissance tokens is a diagnostic target when reliable telemetry exists; it is not a hard stop.
3. Eighty percent of available capacity is an absolute ceiling, never a target. Do not plan work to consume it.
4. Finishing correctly at 20–30% is better than consuming unused capacity.
5. If more evidence is necessary, name the unresolved question and read only what resolves it.
6. If reliable telemetry is unavailable, do not invent token/percentage measurements; enforce the discipline through bounded reads, reuse, scoped exploration, and early handoff.
7. Never save tokens by skipping reasoning, dependency inspection, testing, semantic validation, or blocker reporting.

<!-- SUBAGENT_POLICY_START -->
## Subagents, worktrees, and Git

### Delegation defaults

1. No subagent is the default for a normal bounded Jarvis slice.
2. Normally use no more than one auxiliary subagent. Additional subagents require genuinely independent, bounded, non-overlapping questions whose expected evidence justifies fan-in cost.
3. Do not create nested subagent trees or autonomous swarms unless the active Work Packet explicitly authorizes them.
4. Give each subagent one bounded question or deliverable and only the minimum context needed to answer it.
5. If a subagent discovers an adjacent problem outside its assignment, it reports the dependency and stops rather than expanding scope.

### Default authority

1. Unless the active Work Packet explicitly grants write authority, every subagent is READ-ONLY.
2. A read-only subagent may inspect source, tests, Git history/diffs, Graphify output, and permitted authoritative documentation; it may run only non-mutating diagnostics allowed by the task.
3. A read-only subagent must not edit/create/delete repository files; stage or commit; push; create/update PRs or MRs; change branches/worktrees; merge/rebase/cherry-pick/reset/restore/clean/stash; alter configuration; install or upgrade dependencies; use credentials without explicit authorization; call live Onshape; mutate external systems; modify AGENTS.md/Canon/Work Packets/project rules; or delegate further.
4. No overlapping writes or shared live mutations. The parent agent owns validation, integration, and final completion claims unless the active Work Packet explicitly assigns otherwise.

### Write-capable subagents

A subagent may write only when the active Work Packet explicitly defines all of:

- exact repository/worktree;
- exact base/HEAD requirement;
- exact file or subsystem scope;
- allowed mutations;
- required validation;
- explicit STOP conditions.

Write authority is local to that assignment. It does not imply permission to change architecture, expand scope, alter governance, perform Git integration, commit/push, or mutate external systems. The parent must review the resulting diff before staging, integration, or acceptance.

### Model routing

Model capability does not increase operational authority.

- GPT-6 Sol is preferred for bounded specialist work such as targeted repository reconnaissance, bug localization, regression analysis, bounded code-repair analysis, security/adversarial review, test-gap discovery, exact schema/API investigation, and independent hypothesis checks.
- GPT-6 Astra is preferred when a stronger independent adversarial reviewer is useful: diff review, security review, failure analysis, challenging implementation assumptions, hidden-regression search, and evidence-quality review.
- Neither model becomes the default authority for architecture ownership, task orchestration, scope expansion, broad refactors, repository cleanup, integration ownership, Git/worktree management, long-horizon product decisions, or external-system mutation merely because of model capability.
- These model names are routing hints, not privilege grants. If the named model is unavailable or changes, preserve the role/authority boundary rather than the model label.

### Evidence and fan-in

1. A subagent finding is advisory evidence, not repository truth.
2. Subagent consensus is not a substitute for authoritative evidence.
3. Implementation-driving findings must identify exact supporting files, symbols, diffs, commands, tests, or authoritative sources where possible.
4. Subagent reports should distinguish `VERIFIED`, `INFERRED`, `NOT VERIFIED`, `BLOCKED`, and `RECOMMENDED CHECK`.
5. The parent must independently verify any finding that will drive code changes, schema/API assumptions, CAD semantics, deletion/migration, acceptance claims, architectural decisions, Git integration, or external mutation.
6. Before acting on a recommendation, the parent checks scope, evidence, conflicts with source/tests/Canon/rules, approval gates, blast radius, and reversibility. If materially uncertain, investigate or STOP.

### Worktrees and Git

1. Use an isolated branch/worktree for concurrent work, work that must be isolated, risky slices, or when the active packet explicitly requires it. A trivial safe change does not automatically require a worktree.
2. One coherent vertical slice belongs to one integration line.
3. Inspect status/diff before editing and preserve unrelated user changes.
4. Do not merge dirty, incomplete, unverified, or overlapping work.
5. Do not rewrite history, delete branches, push, merge, or modify `main` unless explicitly requested.
6. Commit only when the Work Packet or user authorizes it; an authorized successful implementation ends with a focused clean commit and reported status.

<!-- SUBAGENT_POLICY_END -->

## Scope, repair, and done

1. Do not perform unrelated cleanup or add frameworks/persistence/services/connectors/provider coupling without an accepted architecture decision.
2. Stop immediately on a real product/architecture fork. Do not reduce a stop condition to after-the-fact reporting.
3. After two failed repair/debug cycles, reassess the failed layer/evidence before continuing.
4. A third cycle is allowed only for a specific bounded correction supported by evidence, not another exploratory attempt.
5. After a third failed cycle, stop and report blocker, failed layer, evidence, and next required decision.
6. The repair cap never permits a broken or unverified completion claim. Preserve the last known good state before further repair.
7. Work is complete only when the accepted slice/user scenario is satisfied at the validation layer appropriate to the claim; required work was not silently skipped; focused/final checks ran; evidence supports claimed layers; unknown CAD semantics remain labeled; assumptions/risks/blockers are explicit; live-call/secret/architecture/Git state is checked; and any required authorized commit exists.

Final report: outcome/scope; files changed; checks actually run/results; assumptions; mutation status/post-state evidence; geometry/dependency/semantic/render/UI layers verified; unverified layers and why; live Onshape calls or `none`; material context/cache behavior; regressions/invariants; Git status/commit; risks/blockers/next decision; terminal state `complete`, `partial`, `blocked`, or `awaiting_approval`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

<!-- HIGH_IMPACT_ACTION_SAFETY_START -->
## High-Impact Action Safety / Blast-Radius Control

Treat safety as a property of the operation's actual effect, not of the
command name. These rules apply to direct commands, scripts, tools,
subagents, generated code, APIs, and indirect side effects.

A potentially destructive, irreversible, broad, privileged, external, or
scope-expanding action MUST NOT execute until its target, scope, impact,
and recovery characteristics are sufficiently established.

### Core invariant

Before a high-impact action, establish:

1. **TARGET** — the exact object(s), path(s), resource(s), process(es),
   branch(es), records, or remote entities that will be affected.
2. **SCOPE** — the authorized boundary and proof that the resolved targets
   remain inside it.
3. **IMPACT** — what will actually be created, changed, overwritten,
   moved, deleted, terminated, published, deployed, migrated, or mutated.
4. **RECOVERY** — whether and how the prior state can be recovered.

If target or scope is empty, unresolved, ambiguous, unexpectedly broad,
or based on an unsafe assumption: **STOP rather than guess**.

Unknown blast radius is a stop condition.

### Required execution pattern

For high-impact operations, prefer:

`inspect -> resolve exact targets -> preview/dry-run when available ->
bound scope -> execute smallest necessary action -> verify actual delta`

Do not use:

`execute broadly -> inspect damage -> attempt recovery`

Prefer reversible and narrowly scoped operations over destructive or broad
ones when both can accomplish the task.

### Filesystem safety

- Do not infer an authorized repository/worktree root merely from the
  shell's current directory.
- Resolve important paths to explicit absolute/canonical paths before
  destructive or recursive operations.
- Never treat an empty/unresolved path variable as the current directory
  or another fallback target.
- Recursive deletion is prohibited by default. When genuinely necessary,
  it must target a specifically identified disposable/generated path
  inside the authorized workspace.
- Never recursively delete `.`, `..`, a drive root, filesystem root,
  home directory, repository parent, workspace root, or an unresolved
  variable.
- Avoid destructive wildcard/glob operations when explicit targets can be
  enumerated.
- Before deleting tracked source, establish that deletion is an intended
  part of the requested change.
- Prefer leaving harmless generated files behind over performing uncertain
  broad cleanup.

### Repository and worktree containment

When operating in a Git worktree, resolve its root explicitly, for example
with `git rev-parse --show-toplevel`.

Unless the task explicitly authorizes otherwise, the assigned worktree is
the writable project boundary.

Do not modify or delete:
- sibling worktrees;
- the primary checkout from a worker worktree;
- parent directories;
- unrelated repositories;
- user home configuration;
- credentials/secrets;
- `.codex`, `.agents`, or other global agent configuration.

A task may explicitly authorize a narrower or broader boundary. Never
expand it implicitly.

### Git safety

Potentially destructive or broad Git operations require the same
target/scope/impact checks.

Do not use broad cleanup/history-destruction as routine recovery,
including operations equivalent to:
- `git clean -fd` / `git clean -fdx`;
- `git reset --hard`;
- broad `git restore` / checkout overwrites;
- force push;
- branch/tag deletion;
- history rewriting.

Use them only when the exact operation is explicitly required and its
blast radius has been established.

If an implementation goes wrong, a dirty worktree is safer than an
uncertain destructive cleanup.

### Bulk transformations

Before repository-wide formatting, generated-code replacement, migration,
search/replace, mass rename, dependency rewrite, or similar bulk mutation:

- establish the intended file/resource set;
- use preview/dry-run or inspect the candidate set when practical;
- compare the resulting delta with the expected scale.

If the actual change is unexpectedly larger or qualitatively different
than expected, **STOP and investigate before continuing**.

Do not normalize, format, stage, or commit unexpected collateral changes
merely because they already occurred.

### Processes, configuration, dependencies, and state

Apply the same containment rules to:
- process termination/restart;
- service management;
- environment/global configuration;
- package install/uninstall/update;
- lockfile regeneration;
- caches and persistent state;
- databases and migrations;
- credentials and authentication state.

Do not use broad process-name kills, global package/config changes,
database resets, cache purges, or destructive migrations when a narrower
operation is sufficient.

### External and remote side effects

Remote actions require explicit scope awareness. This includes:
- push/force-push/merge;
- issue/PR modification or closure;
- releases and publishing;
- deployments;
- cloud-resource mutation/deletion;
- external API writes;
- CAD mutations;
- database writes;
- messages or other externally visible actions.

Local success does not authorize an external side effect.

If a task authorizes one class of external mutation, that does not imply
authorization for adjacent external mutations.

### Shell and script composition

Treat indirect destructive behavior exactly like an explicit destructive
command.

Before executing a script or composed command with high-impact potential,
consider:
- empty or malformed variables;
- wildcard/glob expansion;
- quoting errors;
- relative-path assumptions;
- loops over unexpectedly broad sets;
- pipes / `xargs`-style fan-out;
- recursive behavior;
- symlink/junction traversal;
- generated scripts;
- commands whose semantics depend on current working directory.

A destructive operation hidden inside PowerShell, Python, Make, package
scripts, or another helper remains subject to these rules.

### Unexpected-delta stop condition

An unexpectedly large or unexpected-kind delta is itself evidence that an
assumption may be wrong.

Examples:
- expected 4 changed files, observed hundreds;
- expected one feature mutation, observed broad downstream CAD changes;
- expected one dependency update, observed repository-wide lock changes;
- expected one process, matched many;
- expected one remote object, operation targets a collection.

When this happens: **STOP, preserve evidence, determine the cause, and do
not compound the change.**

### Subagents and delegated tools

Subagents and delegated tools inherit the same authorized boundaries.
They may not broaden filesystem, repository, external-system, or mutation
scope merely because work was delegated.

The parent agent remains responsible for bounding delegated work.

### Final safety rule

When there is meaningful uncertainty about the blast radius of a
potentially destructive or irreversible action, choose the smaller,
reversible action or stop and report the ambiguity.

Do not experiment with destructive operations to discover their scope.
<!-- HIGH_IMPACT_ACTION_SAFETY_END -->
