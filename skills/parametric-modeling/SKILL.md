---
name: parametric-modeling
description: Use when planning, constructing, revising, or repairing a mechanical CAD feature history whose dimensions or dependencies must remain editable.
---

# Parametric Modeling

**REQUIRED FOUNDATION:** Use `cad-engineering-core`.

Build the simplest feature history that expresses intended change.

## Feature order

Prefer this sequence unless the part's design intent requires another order:

1. primary form and durable datums;
2. major functional cuts and features;
3. mounting and interface geometry;
4. repeated geometry represented intentionally;
5. secondary details;
6. finishing features such as fillets and chamfers.

Do not use late cosmetic geometry as the foundation for primary construction.

## Before creating a feature

Check internally:

- Is this primary form, functional detail, repeated detail, or cosmetic finish?
- Should this dimension be shared with another feature or driven by a named
  parameter?
- Will modest upstream edits preserve this feature and its references?
- Would a pattern, symmetry relation, or edit to an existing feature express the
  intent more directly?

Do not print this checklist unless a decision or blocker is material.

## Construction rules

- Use patterns for real repetition; keep separate instances only when they have
  different functions or expected changes.
- Use symmetry when symmetry is intended, not merely visually present today.
- Parameterize important dimensions that change together. Avoid copying the
  same design number into independent features.
- Edit the original responsible feature when its intent is wrong. Do not stack a
  compensating cut, extrusion, or move merely to recover the visible shape.
- Keep feature names and ordering understandable enough to locate primary forms,
  interfaces, repeated details, and finishing operations.

## Existing-model repair

Before repair:

1. identify the first/root failing feature;
2. inspect only the necessary local upstream and downstream context;
3. classify the cause as parameter, reference, constraint, or regeneration;
4. change the smallest responsible source;
5. verify affected downstream state.

Prefer repairing the cause over appending compensating geometry. Do not rebuild
from scratch unless bounded diagnosis shows repair is impractical or the user
requests a rebuild.

## Gate

- `PASS`: the affected history regenerates, repeated intent and shared dimensions
  are represented, and authoritative local evidence supports the requested edit.
- `FAIL`: the history depends on duplicated design numbers, accidental
  repetition, compensation, or an unresolved upstream failure.
- `UNKNOWN`: required feature context or downstream state cannot be inspected.
  Stop before claiming the repair or parametric behavior is complete.
