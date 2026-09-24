# WP006 Native Part Studio Capability Matrix

Revision basis: `cc2b8794565e1d6af37da248995c66b43baef0b0` in worktree
`D:\jarvis-onshape-mcp\.worktrees\D_NATIVE_CAPABILITIES_V5`.

Authoritative public evidence:

- [Onshape Standard Library](https://cad.onshape.com/FsDoc/library.html) documents current native feature definitions, parameter IDs, parameter types, enum names and values, required fields, defaults, and operation semantics.
- [Onshape Features REST API](https://onshape-public.github.io/docs/api-adv/featureaccess/) documents the `BTFeatureDefinitionCall-1406` / `BTMFeature-134` envelope and the REST encodings for query-list, enum, quantity, and boolean parameters. It also states that internal native-feature encodings may change and recommends verifying feature-specific encodings from `getPartStudioFeatures`.

No live Onshape API read or mutation is authorized for WP006. Therefore an
operation whose complete minimum feature definition is absent from the current
public specification remains blocked rather than being reconstructed from
memory, neighboring builders, old payloads, or third-party examples.

| operation | current dedicated native support | exact existing path | authoritative schema completeness | useful minimum subset | unsupported variants | status | action | offline validation possible? | live semantics remaining |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Hole | None | No builder, MCP schema, or dispatch path; `write_featurescript_feature` is only an escape hatch and does not count | Incomplete: public docs identify the `hole` feature and several enums, but omit the complete native feature parameter definition needed for placement, diameter/style, termination, and scope | Plain cylindrical blind hole from selected sketch points | Counterbore, countersink, tapped/threaded, through-all, up-to-next/entity, custom tip, standards-based variants | `BLOCKED_AUTHORITATIVE_SPEC` | Do not implement without complete authoritative native feature encoding | No | Entire native payload, regeneration, geometry/topology, and editability |
| Sweep | None | No builder, MCP schema, or dispatch path; FeatureScript escape hatch only | Incomplete: public docs describe `sweep`/`opSweep` semantics but omit the native feature parameter definition and profile/path/orientation/boolean parameter IDs | One solid NEW sweep from a profile and path | Surface mode, ADD/REMOVE/INTERSECT, orientation and profile-control modes | `BLOCKED_AUTHORITATIVE_SPEC` | Do not implement without complete authoritative native feature encoding | No | Entire native payload, regeneration, geometry/topology, and editability |
| Loft | None | No builder, MCP schema, or dispatch path; FeatureScript escape hatch only | Incomplete: public docs describe `loft`/`opLoft` semantics but omit the native feature parameter definition and ordered section/operation fields | Two-section solid NEW loft | Surface mode, guides, continuity, matching, ADD/REMOVE/INTERSECT | `BLOCKED_AUTHORITATIVE_SPEC` | Do not implement without complete authoritative native feature encoding | No | Entire native payload, regeneration, geometry/topology, and editability |
| Mirror | None | No builder, MCP schema, or dispatch path | Incomplete: public docs identify `mirror` and PART/FEATURE/FACE target classes but omit the native feature parameter definition, target encodings, and mirror-plane parameter | Mirror selected bodies about one planar reference | Feature mirror, face mirror, mixed/other target classes | `BLOCKED_AUTHORITATIVE_SPEC` | Do not implement without complete authoritative native feature encoding | No | Entire native payload, regeneration, geometry/topology, and editability |
| Draft | Yes: neutral-plane subset only | `onshape_mcp/builders/draft.py`; `create_draft` schema and dispatch in `onshape_mcp/server.py`; focused contracts in `tests/builders/test_draft.py` and `tests/test_native_partstudio_capabilities.py` | Complete for the selected subset: native feature `draft`; `DraftFeatureType.NEUTRAL_PLANE`; `neutralPlane`, `draftFaces`, `angle`, `pullDirection`, plus documented false defaults for `tangentPropagation` and `reFillet`; REST types map to enum/query/quantity/boolean BTM parameters | Neutral-plane draft of selected faces with a positive angle and optional reversed pull direction | Parting-line draft, two-sided draft, tangent propagation, re-fillet | `IMPLEMENTED_OFFLINE` | Deterministic builder, closed MCP schema/dispatch, negative preflight, and guarded POST+reread replay implemented | Yes: exact payload, units, angle bounds, schema, dispatch, unsupported variants, network guard, and requested-state verification | Real regeneration, face selection semantics, topology, and editability remain unverified without live CAD |
| Transform / Move body | Yes: translation-only subset | `onshape_mcp/builders/transform.py`; `move_body` schema and dispatch in `onshape_mcp/server.py`; focused contracts in `tests/builders/test_transform.py` and `tests/test_native_partstudio_capabilities.py` | Complete for the selected subset: native feature `transform`; `TransformType.TRANSLATION_3D`; `entities`, `dx`, `dy`, `dz`, and required `makeCopy=false`; REST types map to enum/query/quantity/boolean BTM parameters | Translate selected bodies in world X/Y/Z coordinates; reject a zero vector | Rotation, scale, copy, mate-connector transform, entity/direction-based translation, face movement | `IMPLEMENTED_OFFLINE` | Deterministic builder, closed MCP schema/dispatch, negative preflight, and guarded POST+reread replay implemented | Yes: exact payload, units, zero-motion rejection, schema, dispatch, unsupported variants, network guard, and requested-state verification | Real regeneration, body selection semantics, topology, and editability remain unverified without live CAD |

## Shared path and validation contract

Draft and Move Body must use the existing `apply_feature_and_check` Part Studio
mutation path. Builders must emit deterministic exact payloads and must not add
`libraryRelationType`. MCP handlers must reject unsupported/unknown variants
before calling the mutation path. Offline tests must cover exact payloads,
input failures, tool schemas, dispatch, replay through the guarded transport,
and bounded public mutation results. Offline success is not live-CAD success.
