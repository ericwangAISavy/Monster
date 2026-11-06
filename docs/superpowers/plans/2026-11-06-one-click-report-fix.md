# One-click failure report diagnosis and repair

Report c435e64cf04e44ce9803abfa3e38519d, inspected locally with all nine PNG
hashes verified. Personal screenshots and full report are not checked in.

The original request was “go to startup btn and click once”. Runtime metadata
identifies 7a0abc5, preceding faffb70's normalized coordinate contract. The
screenshot shows Windows 11 Start centered at approximately image (430, 1050),
while the planner proposed (44, 1035). Native cursor telemetry reached that exact
position; the next frame shows Widgets. Verification correctly rejected the
result. A subsequent (15, 1065) click also missed Start. Nearby rejected proposals
did not prevent a farther retry. This is model grounding failure plus an executor
policy defect: the explicit single-click budget was not enforced. There is no
evidence here of DPI conversion failure or false-negative Start verification.
Updating coordinate units alone cannot prove correct semantic grounding.

Observed run: 0/1 successful tasks, two wrong-target click transactions, five
planning calls (8.261 seconds total), two verification calls (1.735 seconds total).
Cleanup confirmed; no human recovery recorded. These timings measure operation
waits, not proof of successful input.

Repair: recognize terminal English “click ... once” requests and latch the budget
from the submitted instruction. Allow only pointer movement and one single click;
reject substitutes and double-clicks. After the click, reject all further input.
Failed/uncertain verification terminates incomplete immediately. Clarification
cannot relax the original budget. Successful verification still requires final
human confirmation. Ordinary compound tasks retain the existing workflow.

Regression coverage: both approval modes, failed/uncertain/successful verification,
double-click rejection, initial keyboard/text/scroll substitution, dispatch guard,
completion review, reset between runs, compound-task scope, and clarification
before/after the click. Regressions were observed failing before their fixes.

Native follow-up: fully quit Monster, pull main, uv sync --frozen, restart. With
Start closed, request “Click the Start button once”, testing both approval modes.
There must be at most one click and no keyboard substitute. Inspect the actual
menu before confirming; if missed, the run must stop incomplete. Check exported
runtime revision and model-to-image coordinate mapping. This Linux environment
cannot establish Windows targeting reliability or the selected model's success
rate; repeat native trials are still required.
