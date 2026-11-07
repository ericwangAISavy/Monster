# App launch stops after typing the Start query

Run: `7627e9f06af1408d909c532bc9c7f173`; reported Windows revision
`f736476480de522df1a1e0f3b763e39f921d7304`. Its committed tree matches the local
`61596a6` baseline (the different revision is a merge, not missing source fixes).
No private screenshots or notification/document content are included here.

## What actually happened

The task requested opening Paint and drawing. The run accepted only two input
transactions: press WIN, then type `Paint`. It never proposed or dispatched Enter
or a click on the matching app's Open control. The final screenshot is still
Windows Search showing Paint as Best match, not a Paint canvas.

| Evidence | Interpretation |
| --- | --- |
| WIN verification reports the menu visible but causal change uncertain | The model conflated current navigation readiness with proving a new change; its statement that the before-image already showed Start conflicts with the retained before frame |
| A proposal describes typing Paint but contains a click | Correctly rejected by the summary/action consistency guard; not the reason the run ended |
| The eventual Paint text transaction is inserted once | Search results update to Paint; a scene-change replan did not duplicate input |
| Two query verification results still say uncertain because prior state/cause is unclear | The visible matching result is intermediate readiness, not application launch |
| Controller enters `confirming`, then receives approval | The uncertainty fallback asks whether the ENTIRE task is complete, even though this was just a search query |
| Outcome becomes complete after that response | Completion came from that whole-task review, not a verified app window or a model-proposed launch action |

The missing launch is a controller-stage error, reinforced by overstrict causal
verification. Enter and normal left-click input are already supported; no new
input permission or backend is needed.

## Correction

For explicit launch goals, recognize the matching app-name query as intermediate
navigation. Retain a pending-launch marker after input and reject completion while
that marker remains. Request the next screenshot-grounded Enter or matching
result/Open click. Do not synthesize input or repeat the query automatically.

If the standalone query remains uncertain, use a distinct `launch_query_review`
with a **Confirm query** button. Approval attests only that the intended query is
visible and continues planning from a fresh scene. It cannot set whole-task human
confirmation. Decline/expiry/stop still cancel. Journal-gated uncertainty remains
blocked for reconciliation.

After a proposed activation, verification requires the actual usable app window,
not a search match or app-details panel. Only a successful activation verification
clears the pending-launch marker. The original task and final human review remain
in place for work after launch, including drawing.

Clarify the planner's app-launch sequence and distinguish navigation readiness
from causality in the verifier prompt. A clearly visible current readiness state
may already have been present. Keep model uncertainty unchanged in parsed output;
never manufacture success by interpreting its free-form evidence.

## Validation scope

Regressions replay the reported WIN/query/uncertainty sequence with fake input,
prove query approval continues to Enter without completing, reject premature
completion even for a verified search result, permit a grounded Open click,
retain uncertainty after failed launch, prevent duplicate typing, and keep final
review separate. Query recognition excludes final document/chat drafts and
unrelated text. UI tests distinguish Confirm query from ordinary final approval.

This corrects launch progression; it does not establish native Windows launch or
Paint drawing reliability. No forced Enter, raw input API, shell command, install,
administrator activation, provider switch or relaxed journal approval was added.

Verified in the current Linux/offscreen environment:

- Targeted controller/provider/journal/desktop regressions: **272 passed**.
- Full suite: **889 passed, 3 skipped**, 129.71s. The skips require Windows UIAccess APIs.
- Ruff, compilation, GUI smoke startup and `git diff --check`: passed.

These checks use fake input and model fixtures. Real Windows launch and the
original drawing goal still require an operator-visible run with the updated code.

### Follow-up: search text reaching the generic text review

The generic “confirm your ENTIRE task” dialog can still be reached when an app
query does not match the narrowly parsed launch instruction and verification is
uncertain. Conversational prefixes such as “Can you open painter…” are now
recognized. Matching queries use an explicit search-readiness postcondition,
independent of whether the proposed expectation incorrectly requires the app
window to be open already. Search readiness still does not establish launch.
Uncertain ordinary text now stops incomplete instead of offering whole-task
confirmation; supervised draft-only composer reviews retain their existing scope.
Hands-free must be enabled in the installed application's provider settings to
suppress intermediate human reviews. An older installation or disabled setting
can still show review dialogs.
