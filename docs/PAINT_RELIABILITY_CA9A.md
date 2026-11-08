# Paint run ca9a291: failure and repair

The uploaded run `ca9a291f39e44df9b520094ce733f8c0` requested
“open painter app and draw simple lines”. Its Windows runtime identified revision
`d6aaa04242823a0c93aa768eff379d02e1e7510f` and used `gemma4:26b`.

## What actually happened

The run dispatched WIN, the full query Paint, Enter, and two tool clicks. Enter
was dispatched at 12.80 seconds. Frame 0011, captured at 13.40 seconds, already
shows an Untitled Paint window, but its client area is still loading. The verifier
incorrectly described this as the search menu remaining open and returned failed.
Unlike uncertain results, failed launch checks previously got no settling recheck.

Later screenshots show Paint's canvas and an already-selected brush. The next
brush click was verified against the leftover *app-window launch* expectation,
not its own tool-selection expectation. A subsequent click described as selecting
the pencil landed near the brush, failed verification, and the model repeatedly
proposed nearly the same coordinates. Three blocked proposals ended the run.
No drawing stroke was attempted. Model-generated evidence in the report is a
fallible assessment, not ground truth; this analysis also inspected the images.

## Root capability gap

The input executor already implemented bounded drag and button cleanup. However,
the provider's decision schema and parser omitted drag entirely. The model could
click, move, and type, but could not express the mouse-down/move/mouse-up
transaction needed to draw a line. Previous launch fixes did not close this gap.

## Changes

- The provider can propose one bounded left-button drag with normalized start
  and end coordinates and a duration between 0.05 and 2 seconds. Both endpoints
  map to screenshot pixels once and receive the monitor offset at dispatch.
- An explicit drawing task with routine risk can run a drag automatically. A
  separate pre-input vision assessment must establish the intended foreground
  app, selected drawing tool, and the complete drag path inside its visible
  editable canvas. The controller binds that result to the exact proposal and
  performs another scene check before dispatch. Draft-only message schemas
  exclude drag. Other drag operations retain review requirements.
- Launch activation gets at most two additional read-only observations, delayed
  750 ms and 1500 ms after the preceding verdict. Enter/Open is never replayed.
  Expected foreground changes during this launch observation are permitted;
  display/bounds changes still stop the run.
- A tool click is not treated as a launch effect merely because launch readiness
  remains pending. If the planner moves on after a failed launch check, a bounded
  separate observation must establish the app window before more editing input.
  The earlier failed verdict remains recorded; it is not rewritten as success.
- Planning prompts explain that an already-selected brush can be used directly
  and that clicks/movement alone cannot draw. Tool-selection verification accepts
  visibly established readiness, but never substitutes a different selected tool.
- Drawing effects get one additional read-only observation when failed/uncertain;
  if still unverified the run stops without an automatic redraw. A visible app or
  selected tool alone never proves that a requested line exists.
- Reports now include the effective hands-free setting, which was absent from
  the uploaded configuration and therefore could not be diagnosed from it.

## Validation and limits

Regression tests cover delayed launch with a foreground change, permanent launch
failure with bounded observations and one Enter, separate app-window observation,
one verified stroke with no approval prompt in hands-free mode, wrong/uncertain
canvas targets with no input, fresh-scene invalidation, cancellation during target
assessment, endpoint validation and negative monitor origins, and no automatic
redraw after an unverified stroke. Existing input tests exercise drag release and
pointer-takeover cleanup.

Tests use fake models/input and real Qt controller sequencing; they do not prove
that this local model will always ground every Windows UI correctly. Native
Windows replay with the selected model remains necessary. There is no claim of
unlimited autonomous task completion. Screenshot/model uncertainty, unsupported
UI interactions, and existing time/input budgets can still stop a task honestly.
