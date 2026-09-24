# WP-004: Onshape API Modeling Stabilization

## Purpose

Stabilize the existing Part Studio write path against the payload shapes that
are supported by repository and upstream evidence. This packet does not add a
new modeling feature family and does not use live Onshape requests.

## Confirmed repairs

- Omit rejected `libraryRelationType: "NONE"` members from the nine affected
  builders.
- Use `booleanBodies`, `operationType`, and the current Boolean operation/body
  grouping established by upstream live evidence.
- Use `instanceFunction`/`BTMParameterFeatureList-1749` and `directionOne` for
  linear FEATURE patterns.
- Use `instanceFunction`/`BTMParameterFeatureList-1749`, `axis`, and
  `equalSpace=true` for circular FEATURE patterns, and require a real axis
  entity instead of guessing a datum-plane edge query.
- Reject pattern counts below one before transport.
- Keep the one-distance chamfer surface on `EQUAL_OFFSETS`; reject
  `TWO_OFFSETS` and `OFFSET_ANGLE` until their required additional arguments
  are deliberately exposed.
- Reject the known-bad X/Y/Z revolve datum-plane edge query before transport;
  the correct replacement axis payload is unresolved.
- Treat HTTP acceptance as transport evidence, not proof of a successful CAD
  mutation. Creation, update, sketch edit, and deletion require an
  authoritative reread for any `verified` result.

## Mutation result contract

MCP mutation results retain the existing `ok`, status, and feature metadata
and add transport, HTTP, regeneration, mutation-verification, change, scope,
and reason fields. `ok=true` requires a verified requested state and a
non-error regeneration state. Accepted but unverifiable writes return
`ok=false` with `mutation_verification="unverified"`; detectable unchanged
writes return `no_effect`.

`FeatureApplyResult.raw` remains an internal compatibility/evidence field.
The MCP-facing serializer explicitly projects an allowlisted bounded result
and never emits `raw`, full Onshape responses, or arbitrary nested response
content. HTTP diagnostics similarly expose only a redacted route and bounded
structured facts.

Sketch removals may be verified by absence. Added or retargeted entities and
constraints require equality of a safe projection of the requested serialized
fields on reread. Server canonicalization that prevents a defensible
comparison yields `unverified`, not success.

## FeatureScript version boundary

Discover and cache the current standard-library version before creating or
uploading a Feature Studio. Compare it with the `FeatureScript <N>;` prelude
and only with imports whose path begins `onshape/std/`. Workspace,
linked-document, custom Feature Studio, Part Studio, and data imports use
reference/microversion semantics and are not compared with the standard
library. A source need not contain a standard-library import.

## Explicitly unresolved

- Whether native extrude needs an `endBound` parameter.
- A valid native default-plane revolve axis query.
- Any reason to pin a global FeatureScript library version.

These are not guessed in WP-004. All tests use synthetic fixtures and mocked
HTTP responses; live Onshape requests remain prohibited.

## Existing-builder compatibility audit (2026-09-24)

This is the Work Packet C revision artifact. It records repository behavior at
`cc2b8794565e1d6af37da248995c66b43baef0b0` before the corrections described
above. Historical issues and pull requests are evidence, not schema authority.
Current public authority was limited to Onshape's
[Feature REST guide](https://onshape-public.github.io/docs/api-adv/featureaccess/),
[FeatureScript standard-library contract](https://cad.onshape.com/FsDoc/library.html),
and current Help pages for
[linear pattern](https://cad.onshape.com/help/Content/PartStudio/linear_pattern.htm),
[circular pattern](https://cad.onshape.com/help/Content/PartStudio/circular_pattern.htm),
[shell](https://cad.onshape.com/help/Content/PartStudio/shell.htm), and
[plane](https://cad.onshape.com/help/Content/PartStudio/plane.htm).

The REST guide explicitly says feature JSON is Onshape's internal format and
may change, and recommends inspecting a UI-created feature through the live
Feature List endpoint for exact current encodings. This packet prohibited live
Onshape calls, so every row distinguishes offline contract evidence from
remaining live acceptance, regeneration, and geometry proof. Graphify could not
provide navigation evidence in this worktree: no graph/wiki/report was present
and the installed launcher failed before traversal. No repair or rebuild was
attempted; reconnaissance fell back once to targeted current source and tests.

| path | current builder/tool exists | historical claim | authoritative current contract available? | base behavior | status | exact residual | action | offline evidence | live semantics remaining |
|---|---|---|---|---|---|---|---|---|---|
| Extrude | Yes: `ExtrudeBuilder`, `create_extrude` | [#8](https://github.com/ReshefElisha/jarvis-onshape-mcp/issues/8) and [#15](https://github.com/ReshefElisha/jarvis-onshape-mcp/issues/15) reported HTTP 400 and blamed omitted `endBound` | Partial: standard-library `extrude` says `endBound` defaults to `BLIND`; exact REST BTM encoding requires a Feature List read | Emits `entities`, `operationType`, `depth`, `oppositeDirection`, `symmetric`; later #12/#13 evidence isolated the 400 to obsolete relation metadata | `ALREADY_FIXED_IN_BASE` | No evidence-backed residual | Preserve the base payload; do not copy #8's unverified `endBound` payload | `test_extrude.py`, `test_payload_stabilization.py`, server tests | Current HTTP acceptance, regeneration, geometry |
| Fillet | Yes: `FilletBuilder`, `create_fillet` | #8 reported HTTP 400 without proving a missing field | Yes for minimum semantics: official `opFillet` requires `entities` and `radius`; other fields are optional | Emits exactly `entities` and `radius`; obsolete relation metadata is absent; #12/#13 later reported live success | `ALREADY_FIXED_IN_BASE` | None | No code churn | `test_fillet.py`, payload invariant, server tests | Current geometry and topology |
| Revolve default-plane axis | Reserved tool; builder fails closed | [#12](https://github.com/ReshefElisha/jarvis-onshape-mcp/issues/12) proved `qCreatedBy(makeId("Top"), EDGE)` resolves four plane-boundary edges | No authoritative default datum-axis REST encoding was available | Raises `UnsupportedOnshapePayloadError` before transport | `ALREADY_FIXED_IN_BASE` | Convenience path remains unavailable | Retain fail-closed behavior | `test_revolve.py`, server no-send test | Replacement axis encoding and all live semantics |
| Boolean | Yes: `BooleanBuilder`, `create_boolean` | PR #11 claimed `booleanBodies`, `operationType`, current enum values and tools/targets grouping | Yes: official `opBoolean` documents `tools`, subtraction `targets`, and `UNION`/`SUBTRACTION`/`INTERSECTION` | Matches the claimed/current contract | `ALREADY_FIXED_IN_BASE` | None | No code churn | `test_boolean.py`, payload invariant, server payload test | Current HTTP acceptance and geometry |
| Linear pattern — PART mode | No supported builder/tool path; only the unused `PatternType.PART` enum exists | No concrete PART-mode defect claim | Yes: official `linearPattern` requires an `entities` Query for PART | Public tool explicitly accepts feature IDs and emits FEATURE | `NOT_REPRODUCED` | No existing PART path to repair | Do not add a new public mode in this bounded packet | Source/schema inventory | A future separately scoped PART implementation |
| Linear pattern — FEATURE mode | Yes: `LinearPatternBuilder`, `create_linear_pattern` | [#13](https://github.com/ReshefElisha/jarvis-onshape-mcp/issues/13) / [PR #14](https://github.com/ReshefElisha/jarvis-onshape-mcp/pull/14) require `instanceFunction` and `directionOne` | Yes: official `linearPattern` publishes feature list, direction, spacing, and total instance-count semantics; Help says minimum count is one | Correct feature/direction payload, but accepted zero/negative counts | `PARTIAL_RESIDUAL_GAP` | Missing deterministic count bound | Reject `count < 1` in builder and schema | Exact builder tests and focused server suite | Current API acceptance, regen, geometry |
| Circular pattern | Yes: `CircularPatternBuilder`, `create_circular_pattern` | PR #14 explicitly left it untouched and unvalidated | Yes: official `circularPattern` requires `instanceFunction` for FEATURE and `axis`; `equalSpace` makes angle a total span; Help requires an axis entity and count >= 1 | Put feature IDs under `entities`, used `axisQuery`, guessed a datum-plane EDGE query, and described a total angle while omitting `equalSpace` | `CONFIRMED_GAP` | Wrong feature/axis fields, unsafe guessed axis, wrong angle semantics, missing count bound | Require `axisEntityId`; emit `instanceFunction`, `axis`, `equalSpace=true`; reject `count < 1`; update MCP schema/dispatch | Exact builder payload tests, server mapping/no-send tests, payload invariant | REST acceptance, regeneration, geometry/topology |
| Chamfer | Yes: `ChamferBuilder`, default-only `create_chamfer` | Obsolete relation metadata only | Yes: official `opChamfer` requires `width` for `EQUAL_OFFSETS`, `width1`+`width2`+`oppositeDirection` for `TWO_OFFSETS`, and `width`+`angle`+`oppositeDirection` for `OFFSET_ANGLE` | Emitted only `width` for all three enum values | `PARTIAL_RESIDUAL_GAP` | Direct-builder non-equal modes produced incomplete payloads | Preserve `EQUAL_OFFSETS`; reject richer modes before transport rather than invent arguments | Builder parameterized rejection and server no-send test | Current API acceptance and geometry for equal-offset mode |
| Shell | Yes: `ShellBuilder`, `create_shell` | Obsolete relation metadata | Yes for supported semantics: current Help confirms removed faces, thickness, and inward/outward direction | Matches prior live-backed payload; obsolete metadata absent | `ALREADY_FIXED_IN_BASE` | None | No code churn | `test_shell.py`, payload invariant, server wrapper test | Current API acceptance and geometry |
| Offset plane | Yes: `OffsetPlaneBuilder`, `create_offset_plane` | Obsolete relation metadata | Yes for supported semantics: current Help confirms offset from plane/planar face with distance and direction flip | Matches prior live-backed payload; obsolete metadata absent | `ALREADY_FIXED_IN_BASE` | None | No code churn | `test_offset_plane.py`, payload invariant, server reference/no-send tests | Current API acceptance and resulting datum geometry |
| Constraint-first sketch serialization | Yes: `SketchBuilder.add_constraint_spec` | #13/#19 reported rejected relation metadata | Exact REST sketch internals require a live Feature List read; current serializer is local implementation truth | Shared serializer omits `libraryRelationType` | `ALREADY_FIXED_IN_BASE` | None | Keep payload-level invariant; no textual source test | `test_sketch_constraints.py`, `test_payload_stabilization.py`, sketch builder/server tests | Live solver and dimension semantics |
| Edit-sketch constraint serialization | Yes: `edit_sketch(add_constraints=...)` | #19 follow-up named the edit path | Same boundary as constraint-first serialization | Reuses `serialize_constraint`; compare-only ignore keys do not emit metadata | `ALREADY_FIXED_IN_BASE` | None | No duplicate serializer or test | `test_sketch_edit.py`, feature-apply edit tests, server edit tests | Live reread equality and solver semantics |
| `variableThickness` / `variableDepth` | Yes, as optional scalar variable-name plumbing on shell/thicken/extrude | #8 mentioned failures without a separate schema diagnosis | Quantity expressions support variable references; no variable-per-entity feature is advertised | Maps names to `#variable` expressions; stale numeric values are cleared where the builder carries them | `PRESENT_NO_GAP` | No separate capability to resurrect | Preserve current paths | Extrude/thicken/shell builder and server tests | Live variable resolution |

### Diagnostics boundary

Builder-specific validation in this slice now rejects known invalid counts,
guessed circular axes, and unsupported chamfer parameter combinations before
network I/O. Generic HTTP/result error-body shaping remains Branch A territory;
this audit found no reason to duplicate that logic here.
