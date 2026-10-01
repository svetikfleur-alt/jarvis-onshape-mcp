---
name: reference-stability
description: Use when selecting, reviewing, or repairing CAD references that may become fragile after upstream dimensional or topology changes.
---

# Reference Stability

**REQUIRED FOUNDATION:** Use `cad-engineering-core`.

Choose references by intended meaning and likely survival, not by immediate
convenience.

## Preference hierarchy

When alternatives express the same intent, prefer:

1. standard origins and datums;
2. explicit construction or datum geometry;
3. intentional sketch/reference geometry;
4. stable feature or body references;
5. generated faces and edges when necessary.

This hierarchy is not absolute. A face reference is legitimate when that face is
the functional interface and its identity is expected to survive. Extra datum
geometry is worthwhile only when it materially reduces fragility or clarifies
intent.

## Survival check

Before accepting a material reference, ask internally:

> If the upstream dimension changes modestly, is this reference likely to
> survive and still mean the same thing?

If not, use a more durable datum/reference or record why the topology dependency
is necessary.

Avoid:

- primary construction driven from cosmetic or late finishing features;
- accidental face/edge dependencies chosen only because they are easy to select;
- unnecessarily deep reference chains;
- treating an ambiguous or missing entity identifier as a verified reference.

## Repair

When a downstream reference fails, find the first feature whose intended
reference is no longer satisfied. Inspect bounded local context, replace the
fragile source with the smallest durable reference that preserves intent, and
verify affected downstream regeneration. Do not mask the break with unrelated
geometry.

## Gate

- `PASS`: authoritative feature context identifies the reference and bounded
  upstream-change reasoning supports its intended survival.
- `FAIL`: the reference is accidental, ambiguous, missing, or depends on late
  topology without an engineering reason.
- `UNKNOWN`: feature/reference context is unavailable or topology survival cannot
  be established. Preserve the uncertainty; do not invent an edge or dependency.
