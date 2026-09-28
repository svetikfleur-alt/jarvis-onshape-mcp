---
name: cad-engineering-core
description: Use when creating, editing, repairing, or reviewing mechanical CAD where design intent, editability, manufacturability, or engineering completion matters.
---

# CAD Engineering Core

Use this doctrine with the provider/tool skill. It governs engineering decisions;
it does not replace tool schemas or mutation-verification rules.

## Core standard

A good CAD model is a durable expression of design intent, not merely a valid
shape. Another engineer should be able to understand the feature history, change
important dimensions, regenerate the model, trace dependencies, and continue
without first cleaning up agent artifacts.

Treat these as separate quality layers:

| Layer | Question |
|---|---|
| Shape correctness | Does the geometry match the requested form and function? |
| Parametric quality | Do sketches, parameters, references, and feature order preserve intended edits? |
| Manufacturing quality | Is the part physically reasonable for the intended process and assembly? |
| Document quality | Is the document understandable and free of agent-created clutter? |

`HTTP accepted`, `regeneration OK`, and “all requested objects exist” prove none
of the higher layers by themselves.

## Invariants

- Preserve design intent and editability when choosing between geometrically
  equivalent constructions.
- Use explicit, meaningful parameters for dimensions that drive function or are
  likely to change together. Do not duplicate one design number across
  independent features without a reason.
- Keep feature history ordered and readable. Prefer the smallest responsible
  edit over corrective geometry added later.
- Create support geometry or document elements only when they carry durable
  intent. Reuse suitable existing studios; never use Feature Studios as generic
  scratch space or create one Variable Studio per variable.
- Track temporary artifacts created by the agent and remove them only when their
  ownership and safe cleanup are known. Never delete unknown user elements for
  tidiness. Distinguish baseline/default elements and user work from
  agent-created clutter before cleanup.

## Evidence and autonomy

For each material gate, classify the result:

- `PASS`: authoritative evidence supports the intended condition.
- `FAIL`: authoritative evidence contradicts it; repair before completion.
- `UNKNOWN`: the necessary capability or authoritative state is unavailable.
  Report the limitation; do not convert visual appearance, constraint counts, or
  remembered state into proof.

The agent may choose routine construction strategy. Stop or ask when materially
different product requirements are plausible, a destructive target is
ambiguous, a physical interface dimension cannot be safely inferred, or a
critical decision lacks authoritative model state.

## Efficient checkpoints

- During build: use local sketch/feature results; do not reread or render the
  entire model after every mutation.
- After a major stage: inspect a compact model-state summary and affected
  dependencies.
- Visual inspection: use it after primary form, after major functional geometry,
  before and after the single bounded refinement pass, and at final review when
  the capability exists.

Perform checks internally. Report material decisions, blockers, and final
quality evidence—not the full checklist before every action.
