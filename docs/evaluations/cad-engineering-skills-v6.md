# CAD Engineering Skills V6 — Scenario Evaluation

Use these scenarios to evaluate decisions, not phrase recall. Give an agent the
scenario and the relevant skill set, then compare its proposed actions with the
observable criteria below. Runtime CAD execution is optional; when authoritative
model evidence is unavailable, the correct result may be `UNKNOWN`.

## Result meanings

- `PASS`: the response chooses the required engineering action, rejects the
  prohibited shortcut, and requests or uses evidence appropriate to the claim.
- `FAIL`: the response accepts geometric/regeneration success as sufficient,
  chooses the prohibited shortcut, invents unavailable evidence, or expands the
  task beyond one bounded repair/refinement pass.
- `UNKNOWN`: the response identifies the missing authoritative evidence and
  withholds the affected completion claim. `UNKNOWN` is not failure unless the
  agent could have used an available bounded inspection and skipped it.

## E1 — Underconstrained production rectangle

**Situation:** A rectangular production sketch extrudes successfully. An
authoritative sketch-health capability reports remaining degrees of freedom.

**Expected behavior:** Identify the intended datum, add the missing geometric
relations or design dimensions, and re-inspect constraint health before building
dependent features. The sketch passes only when remaining freedom is intentional
or zero and its design intent is represented. Proceeding because the extrusion
regenerates is `FAIL`.

## E2 — Fully fixed but poorly constrained sketch

**Situation:** Every entity in a mounting profile is held by blanket `Fix`
constraints. The sketch reports fully constrained.

**Expected behavior:** Treat constraint health and design intent as separate
gates. Replace arbitrary fixing with origin/datum anchoring, geometric relations,
and meaningful dimensions where the edit scope permits. Accepting “fully
constrained” without testing edit intent is `FAIL`. If the fix constraints cannot
be inspected authoritatively, report that part as `UNKNOWN`.

## E3 — Four repeated holes modeled independently

**Situation:** Four equal, equally spaced mounting holes are separate sketches or
cuts with repeated numeric dimensions.

**Expected behavior:** Represent real repetition with one seed plus a pattern or
intentional sketch symmetry, and share the driving dimensions. Preserve separate
features only when the holes have distinct functions or future change behavior.
Copying the same number into four independent features without justification is
`FAIL`.

## E4 — Primary construction references a late generated edge

**Situation:** A locating sketch for a primary interface references an edge made
by a late chamfer. A modest upstream size change may replace that edge.

**Expected behavior:** Prefer an origin, datum/construction plane, intentional
sketch geometry, or a stable body/feature reference. A generated edge is
acceptable only with a stated reason and survival check. Referencing the chamfer
edge merely because it is convenient is `FAIL`.

## E5 — Compensation instead of editing original intent

**Situation:** A boss is 2 mm too short. The original boss feature is editable,
but adding a second 2 mm extrusion would make the visible shape correct.

**Expected behavior:** Edit the original boss dimension and verify affected
downstream features. Add compensating geometry only when the source cannot be
safely edited and the reason is recorded. Stacking the corrective extrusion by
default is `FAIL`.

## E6 — Functional but visually crude enclosure

**Situation:** Body, lid, fasteners, clearances, and regeneration are correct,
but proportions and edge treatment still look like a first valid box.

**Expected behavior:** Perform one bounded review, select one to three modest
changes with material engineering or visual value, apply one refinement pass,
and stop after rechecking affected evidence. Endless beautification is `FAIL`;
stopping without considering a bounded refinement is also `FAIL`.

## E7 — Duplicate Variable Studio

**Situation:** A document already contains a suitable Variable Studio. A new
dimension is needed for the same design family.

**Expected behavior:** Reuse the suitable studio and add a meaningful shared
variable. Do not create one studio per variable or use a Feature Studio as
scratch space. Do not delete an unfamiliar studio merely for tidiness; classify
ownership first. Creating duplicate support elements or deleting unknown user
elements is `FAIL`.

## E8 — Downstream failure after an upstream change

**Situation:** Several downstream features fail after a primary width changes.

**Expected behavior:** Find the first/root failing feature, inspect bounded local
upstream and downstream context, classify the cause as parameter, reference,
constraint, or regeneration failure, change the smallest responsible source,
then verify downstream state. Rebuilding the model or appending compensating
features before this diagnosis is `FAIL`. If critical authoritative state is
unavailable, stop with `UNKNOWN` rather than guessing.

## Coverage review

The set covers sketch underconstraint, false confidence from blanket fixing,
intentional repetition, fragile topology references, edit-versus-compensate,
bounded visual refinement, document hygiene, and root-cause repair. A release
review should also confirm that the skills use capability-oriented inspection
language, distinguish `PASS`/`FAIL`/`UNKNOWN`, and do not require per-feature
full-model rereads or renders.
