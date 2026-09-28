# Jarvis V6 performance and reliability evidence

This note records the implementation-driving findings for the V6 Performance,
Mutation Truth & State Hardening packet. The benchmark figures are historical
input from the packet; source behavior and test evidence refer to this branch.

## Execution paths

Part Studio create operations (Extrude, linear feature Pattern, Shell,
Chamfer, Draft, and Move Body) share this path:

1. `server.call_tool` resolves the explicit document/workspace/element target
   and builds the feature payload.
2. the server mutation wrapper delegates to `api.feature_apply`;
3. the helper sends one feature POST;
4. a `GET /features?featureId=<created id>` rereads only the created feature;
5. regeneration status and canonical requested fields are compared;
6. matching in-process feature contexts are patched from that reread and their
   topology-dependent evidence is invalidated;
7. the MCP adapter emits the semantic result and a bounded change summary.

Existing-feature update uses one targeted feature GET for preflight, one POST,
and one targeted feature GET for verification. It no longer needs either of
its former full-tree reads. A failed or unavailable reread remains `unverified`;
an actual field mismatch or regeneration error remains non-verified.

Pure feature creation can safely append its authoritative feature delta while
retaining upstream cached definitions. An update to an existing feature marks
the wider cached tree `partial`: its reread proves the changed feature, but does
not relabel old downstream evaluated values or statuses with the new revision.
Tree/dependency tools fail closed on partial or stale contexts. Feature deletion
replaces the cache from its complete post-delete snapshot. Variable Studio
updates conservatively invalidate every known Part Studio context and topology
snapshot in the same document workspace.

Sketch-on-face preflight validates the face against the shared topology
snapshot. An absent or invalidated snapshot is read once. A missing ID in a
fresh cache receives one authoritative refresh and one reevaluation; remaining
ambiguity fails closed. No retry loop is present.

## Proven root causes

- The shared feature comparator treated parameter display metadata, Onshape
  wire-version suffixes, and the ordering of deterministic/feature reference
  sets as semantic. This caused canonical server responses to be classified as
  mismatches or unverified.
- Quantity updates compared expression strings even when both sides supplied
  authoritative SI values, so `15 mm` versus `0.015 m` could remain
  unverified.
- Create verification and both update reads called the unfiltered feature-list
  endpoint despite the official `featureId` filter.
- Model-context snapshots were not reconciled after mutation, and topology
  enumeration had no shared freshness/invalidation state.
- face and edge geometry signatures included coordinates. A pure body
  translation therefore looked like wholesale topology removal/addition.
- MCP change serialization returned every changed face and edge without a
  bound.
- document creation resolved only Part Studios and retained no complete
  bootstrap element baseline, so default Assembly/BOM provenance was lost.
- server and skill instructions explicitly requested `describe_part_studio`
  after every visible mutation, including already-verified mutations.

## Agent execution overhead classification

The original benchmark did not retain a per-invocation MCP trace, so individual
calls inside its aggregate counts cannot all be classified defensibly. The
following classifications are supported by the packet evidence:

| Observed interaction | Classification | Evidence boundary |
|---|---|---|
| 22 transmitted CAD mutations | `ENGINEERING_DECISION_REQUIRED` | Each selected a distinct requested CAD operation. |
| Later reads used to settle 11 initially disputed successful mutations | `VERIFICATION_FOLLOWUP` | The packet states all 11 were subsequently proven correct. |
| Full feature refresh immediately used only to re-prove a mutation | `REDUNDANT_READ` | Exact subset of the 11 aggregate refreshes is not available. |
| Three rejected Sketch attempts followed by explicit topology rereads | `STALE_STATE_RECOVERY` | Rejection occurred before Jarvis dispatch; internal recovery applies once a call reaches Jarvis. |
| Move Body's unbounded topology delta | `LARGE_RESPONSE_OVERHEAD` | Coordinate-based signatures and uncapped arrays were both present. |
| Targeted semantic reread and canonical comparison inside a mutation | `DETERMINISTIC_INTERNAL_WORK` | No model judgment is required between POST and requested-state verification. |
| Selecting a new face/edge based on geometry after regeneration | `ENGINEERING_DECISION_REQUIRED` | The model must choose among authoritative candidates. |

The process-local `get_execution_metrics` result now reports observable MCP
invocations, approximate model/tool round trips, elapsed time, response bytes
by tool family, HTTP/mutation/broad/targeted/topology counts, cache events, and
semantic verification reads. HTTP attempts, completed responses, and transport
failures are distinct, and raised MCP invocations remain counted. It stores no
arguments, IDs, credentials, or raw CAD payloads.

Deterministic work now internalized under the existing packet includes targeted
post-mutation verification, context patching, topology invalidation, and one
bounded stale-reference refresh. A later Execution Protocol Optimization packet
can evaluate context-handle target reuse across mutation schemas, explicit
logical-operation correlation IDs, and compact topology selection projections.
It must keep separate model turns wherever geometry selection or design intent
requires judgment.

## Sketch constraint engineering follow-up

Jarvis can already author geometric and dimensional constraints through
`create_sketch` and `edit_sketch`. Coordinate-first helpers may intentionally
create geometry without the dimensional/positional constraint set needed for a
production sketch. Current authoritative feature responses expose entities,
constraints, and regeneration state, but the inspected official feature API
and FeatureScript sketch documentation do not expose a documented
fully-constrained flag or degrees-of-freedom result.

A separate **Jarvis Sketch Constraint Engineering** packet should be bounded to:

1. establish, by official API/schema evidence and a disposable live probe,
   whether any supported endpoint returns sketch solver DOF or full-constraint
   status;
2. if available, add one compact read projection and deterministic quality
   classification;
3. if unavailable, define a conservative constraint-coverage analyzer that
   reports only proven missing dimensions/anchors and never claims full
   constraint from constraint counts;
4. cover fully constrained, underconstrained, conflicting, construction, and
   externally referenced sketches;
5. keep automatic constraint insertion and a sketch-subsystem rewrite outside
   that packet unless separately approved.

The production invariant is: a profile existing and regenerating does not by
itself establish production-sketch completeness. Until authoritative solver
evidence exists, completeness remains `UNVERIFIED`.
