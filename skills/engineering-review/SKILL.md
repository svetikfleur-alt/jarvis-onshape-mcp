---
name: engineering-review
description: Use when a mechanical CAD build or repair is about to be declared engineering-complete, or when a valid model may still need bounded refinement.
---

# Engineering Review

**REQUIRED FOUNDATION:** Use `cad-engineering-core`.

Perform one bounded final review. Reuse current authoritative evidence; do not
reread or render unchanged stable state merely to repeat a check.

## Review gates

Classify each applicable category `PASS`, `FAIL`, or `UNKNOWN`:

### Functional

- Requested functions are present.
- Interfaces, clearances, and motion assumptions are sensible.

### Parametric

- Production sketches are adequately constrained according to intent.
- Important dimensions are parameterized and existing features remain editable.
- Affected downstream features regenerate healthily.

When authoritative sketch-health evidence exists, require:

`UNINTENTIONALLY_UNDERCONSTRAINED_PRODUCTION_SKETCHES = 0`

Without that evidence, mark sketch health `UNKNOWN`; never infer it from a render,
UI coloring the agent cannot see, or constraint counts alone.

### Structural

- References are reasonably stable and dependency depth is justified.
- Repetition and symmetry are represented intentionally.

### Manufacturing

- The model is printable, machinable, and assemblable as intended.
- Wall thickness, clearances, tool/process access, and feature geometry are
  physically reasonable for the stated process.

### Document

- Feature history is understandable.
- No unnecessary tabs, duplicate support studios, or known temporary artifacts
  created by the agent remain.
- Unknown user elements are preserved.

### Design / visual

- Proportions and edge treatment look deliberate and coherent.
- Repeated elements look intentional rather than accumulated.
- The result avoids an obvious “first valid box” appearance.

The agent does not automatically see the live viewport, camera, sketch colors,
UI-only warning icons, or live-sync timing. Use compact model state, sketch or
feature inspection, measurements, and visual snapshots only when those
capabilities exist. Otherwise report the affected gate as `UNKNOWN`.

## One refinement pass

If one to three modest changes would materially improve engineering quality or
visual coherence, select them, apply one bounded refinement pass, and recheck
only affected evidence. Then stop.

Do not beautify indefinitely, introduce a new product requirement, or optimize
subjective details after functional and engineering quality are good. A failed
functional, parametric, structural, manufacturing, or document gate is repair,
not optional refinement.

## Completion

Declare engineering completion only when required gates pass and every material
unknown is disclosed. Report concise evidence, material decisions, blockers, and
unverified UI/live layers. Geometric validity or regeneration alone is not
engineering completion.
