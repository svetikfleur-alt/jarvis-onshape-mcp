# Jarvis V6 Agent-Facing Execution Protocol

## Accepted outcome

Make an ordinary, already-resolved Part Studio feature mutation a single agent-facing
operation that returns a bounded semantic result with authoritative mutation truth. Keep
engineering choices with the caller, reuse the existing `feature_apply` verifiers, preserve
the existing low-level tools, and avoid changes to WorkingState, caching, invalidation policy,
or transport semantics owned by `codex/perf-reliability-v6`.

## Current protocol forensic map

| Logical operation | Current agent calls | Internal mechanics | Classification and avoidable follow-up |
| --- | ---: | --- | --- |
| Create Sketch | 1 mutation, commonly 1 `describe_part_studio` | resolve datum locally; POST; authoritative feature reread | mutation choice is `ENGINEERING_DECISION_REQUIRED`; apply/reread is `DETERMINISTIC_INTERNAL_WORK`; the success hint creates a `VERIFICATION_FOLLOWUP` |
| Create Extrude | 1 mutation, commonly 1 describe/topology read | optional face-placement GET for REMOVE direction; optional change snapshots; POST; authoritative feature reread | direction override is an engineering choice; lookup/apply/reread are deterministic; post-success describe is a verification follow-up |
| Update Extrude | 1 `update_feature`, commonly 1 describe | authoritative pre-read; one POST of the patched feature; authoritative reread; requested-parameter comparison | patch choice is engineering; merge, POST, comparison are deterministic; no second POST is used for verification |
| Linear Pattern | 1 mutation plus later topology read when another picked target is needed | optional change snapshots; POST; authoritative feature reread | seed/direction/count are engineering; verification is deterministic; a topology read is `TARGET_RESOLUTION` only when the next feature needs it |
| Shell / Chamfer / Draft | 1 mutation, commonly 1 describe | optional change snapshots; POST; authoritative feature reread | selected faces/edges and dimensions are engineering; verification is deterministic; success hint adds a verification follow-up |
| Move Body | 1 mutation, commonly 1 describe | change snapshots; POST; authoritative feature reread | body selection/vector are engineering; large changed face/edge lists are `LARGE_RESPONSE_OVERHEAD` |
| Inspect feature | 2 calls without an existing context (`start_model_context`, then `inspect_feature`) | one full feature read, cached index build, bounded projection | context setup is deterministic; the first call is avoidable `TARGET_RESOLUTION` overhead for one feature |
| Inspect topology | 1 call (`list_entities`, `get_body_details`, or describe) | body-details read, and sometimes FeatureScript frame enrichment/renders | target picking is an engineering decision; unfiltered topology and renders are large-response overhead |
| Verify mutation | mutation result plus commonly 1 describe | mutation helpers already perform authoritative reread and canonical comparison | ordinary follow-up is redundant when `mutation_verification=verified`; geometry/render checks remain separate engineering evidence when the next decision needs them |

The largest response families are `describe_part_studio` (full topology plus images), raw
`get_features`, unfiltered topology/body-detail reads, and mutation `changes` containing
face/edge lists. The server instructions currently tell agents to call
`describe_part_studio` after every mutation even though Part Studio mutations already return
authoritative requested-state verification.

## Design

Add an adapter-only `execution_protocol` module. It will:

- project the existing `FeatureApplyResult` public fields into a versioned compact execution
  result;
- allowlist and bound requested canonical parameters;
- collapse geometry diffs into counts/scalars in compact mode while retaining the existing
  bounded low-level result in explicit diagnostic mode;
- build compact feature/Part Studio projections from one feature read and one parts read;
- collect process-local invocation and serialized-response-size counters with explicitly named
  follow-up proxies.

Expose three preferred MCP operations:

1. `execute_feature` selects one of the existing create/update feature handlers and delegates to
   it exactly once. It contains no CAD planning and does not implement verification logic. The
   supported operations are create Sketch/Extrude/linear Pattern/Shell/Chamfer/Draft/Move Body
   and update existing feature.
2. `inspect_feature_compact` performs one authoritative feature read and returns one bounded
   normalized feature without requiring a context-start round trip.
3. `get_compact_model_state` performs bounded feature and parts reads and returns identity,
   regeneration summary, body summary, recent feature rows, failures, canonical parameters,
   and explicit unavailable/stale categories. It never returns topology.

`get_execution_protocol_metrics` exposes bounded process-local measurements. Existing low-level
tools remain registered and unchanged as advanced/diagnostic surfaces. No LLM, planning,
multi-feature decision, cache, WorkingState, new verifier, or transport behavior enters Core.

## Validation boundary

Offline tests prove schema/dispatch, one delegated mutation, preserved verification states,
compact structural absence, diagnostic evidence retention, one-read inspection, compact model
state, and metric accounting. They do not prove Onshape regeneration, geometry, topology,
rendering, or UI behavior. Focused live validation is limited to a newly created disposable
document after the entire offline suite and Ruff pass.
