---
name: sketch-engineering
description: Use when creating, editing, diagnosing, or accepting production sketches, especially when constraint health or design freedom is uncertain.
---

# Sketch Engineering

**REQUIRED FOUNDATION:** Use `cad-engineering-core`.

Production sketches must be both adequately constrained and constrained according
to the intended design. These are separate requirements.

## Procedure

Before leaving a production sketch:

1. **Establish reference intent.** Choose the origin, datum, construction
   geometry, or intentional external reference that locates the sketch.
2. **Create minimal geometry.** Use only the entities needed to express the
   profile and its construction logic.
3. **Apply geometric relations.** Encode shape logic with coincident,
   horizontal/vertical, parallel/perpendicular, tangent, concentric, symmetry,
   equal, and other appropriate relations.
4. **Apply design dimensions.** Dimension function and change behavior, not every
   seed coordinate. Reuse shared parameters where dimensions change together.
5. **Inspect constraint health.** Use an authoritative sketch-health or solver
   capability when available.
6. **Resolve unintended freedom.** Add the missing relation, datum anchor, or
   design dimension that explains the intended degree of freedom.
7. **Gate progress.** Proceed only when remaining freedom is intentional and the
   sketch expresses the intended edit behavior.

Prefer the fewest independent constraints that fully express intent. Redundant
constraints make diagnosis and later edits harder.

## Do not hide freedom with Fix

Do not apply blanket `Fix` constraints merely to make a sketch report fully
constrained. A fully fixed arbitrary sketch may be uneditable and carry no useful
design relationships.

Use `Fix` only when immobility of imported, legacy, or deliberately frozen
geometry is itself the intent and record that reason. Otherwise replace it with
an origin/datum anchor, geometric relations, and meaningful dimensions.

## PASS / FAIL / UNKNOWN

- `PASS`: authoritative health shows no unintended remaining freedom, and a
  design-intent review confirms that anchors, relations, and dimensions encode
  how the sketch should change.
- `FAIL`: authoritative health shows unintended freedom, solver failure, or
  arbitrary fixing; do not build dependent production features until repaired.
- `UNKNOWN`: authoritative sketch health or constraint details are unavailable.
  State what cannot be verified. Do not infer completion from blue/black UI
  coloring the agent cannot see, a render, successful extrusion, or constraint
  counts alone.

An intentionally flexible layout or exploratory sketch may retain freedom when
that purpose is explicit. Do not use that exception for production profiles.
