# V2 qualification increment

The user requested the remaining reliability work. UI-Venus is not hosted yet;
this Linux workspace cannot establish native Windows or live model qualification.

Implement a bounded screenshot grounding benchmark with independent region/action
oracles, repeated explicit trials, per-DPI accuracy, latency percentiles, safe
failure categories and metadata-only reports. Recorded-response replay tests the
harness; only explicit live requests measure model grounding. No oracle data enters
model prompts. No capture or input occurs in the benchmark.

Add environment preflight and a machine-readable native acceptance record covering
input, focus, geometry, screenshots, stop, cleanup and draft trials. Missing,
failed, simulated, incomplete and unmeasured evidence must block readiness. A
readiness assessment is advisory, not an execution permission or authentication
of user-authored evidence. Existing controller/worker/approval paths stay intact;
UI-Venus remains disconnected from input until exact runtime and native evidence
exist. Do not fake a verifier or turn a preview into an authorized action.

Validation: fail-first scoring/report/CLI tests; existing fixture replay; full
portable suite, lint, offscreen startup. Push completed portable work to main and
report precise live/native prerequisites. Later steps: actual Windows trials,
UI-Venus serving, held-out benchmark, independent verifier and supervised bridge,
then repeated draft-only end-to-end acceptance. Character state crossfades are a
separate visual refinement; they do not block this engine increment.

## Completed portable evidence

- 448 tests passed in the final full run, including 32 new grounding/qualification
  cases covering independent scoring, history isolation, bad evidence, deadline
  accounting, cancellation, source binding, overflow and manual recovery.
- Ruff, whitespace checks, frozen dependency sync and offscreen startup passed.
- Recorded replay: 9/9 predictions; model accuracy and latency intentionally null.
- Native preflight: Linux/unavailable, no screenshots or input dispatched.
- Independent review findings about replay cancellation and contradictory evidence
  were reproduced with failing tests, fixed and re-reviewed without important blockers.

This is not native Windows evidence, live model accuracy, task reliability or
completion of the stage 3 controller bridge. No model server was available.
