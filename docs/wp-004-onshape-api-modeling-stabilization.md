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
- The current circular-pattern direction/axis schema beyond omission of the
  rejected shared field.
- Any reason to pin a global FeatureScript library version.

These are not guessed in WP-004. All tests use synthetic fixtures and mocked
HTTP responses; live Onshape requests remain prohibited.
