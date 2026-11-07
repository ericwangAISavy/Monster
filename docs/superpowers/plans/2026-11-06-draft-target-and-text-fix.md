# Draft target and text repair

Report 9a06: task requests a hello draft in current Slack. Six locally inspected PNG
hashes verified; frames show Chrome/ChatGPT, not Slack. Model proposes press H,
not atomic text, and controller permits it. Native two-event receipt cannot prove
text insertion; unchanged post-input image supports failed verification. Three
repeat rejections correctly prevent replay. Probe passes; server load dominates
latency but is not this correctness failure. No personal screenshots committed.

Repair under existing scope:
- Draft keyboard input must use atomic text, not physical character presses or
  editing shortcuts. Schema removes press choices for draft tasks and explains
  text payloads with an example. Existing send/duplicate safeguards remain.
- Independent screenshot-only target check before draft input: ready, needs_focus,
  wrong_target, uncertain. Check requested app identity and editable composer,
  not task wording alone. Slack in a browser is legitimate; ChatGPT is not Slack.
- Bind target check to exact reviewed proposal; after the model wait recapture and
  compare against checked scene before dispatch. Wrong/unknown target stops with
  no input. Correct app but uncertain text focus asks existing focus review, then
  rechecks fresh observations; human focus evidence never means task completion.
- Log target check stage/evidence/timing in RAM, preserve review/stop and no replay.

Tests: report-shaped press H rejected beforeinput both approval modes; provider
schema/prompt exposes whole text; wrong-target check prevents input; ready permits
one text; target change while awaiting check prevents dispatch; focus review,
proposal mutation, cancellation/late result and proof invalidation on replanning.
Run full tests/lint/startup and independent review; commit/push normally.

Observed evidence: 0/1 successful drafts, one physical H transaction to the
foreground browser, no duplicate input replay, four planning calls/52.514s total,
one visual check/6.821s, cleanup confirmed. Compatibility passed in 19.090s.
Keyboard approval was used and recorded cursor position stayed unchanged. No
screenshot/DPI conversion or companion-animation failure is demonstrated here.
The app's scope validation failed; successful event insertion did not imply text
in the requested composer. Native Windows target/typing reliability must still be
retested; portable regression evidence is not native qualification.

Validation: 309 tests passed in 42.11s; ruff/diff checks passed; startup exited 0.
Independent review found no blockers; 188 focused provider/supervised tests passed.
Tests include long target wait followed by fresh capture, invalid status types,
malformed assessments, correct-app focus review, changed scenes, cancellation and
proposal mutation. Visual/model checks are fallible, not deterministic guarantees.
