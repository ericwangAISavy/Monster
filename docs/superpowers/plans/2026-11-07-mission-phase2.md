# Safe mission GUI integration — Phase 2

**Goal:** Opt-in two-subtask mission using the existing supervised GUI engine.
**Spec:** User-provided CODEX_MONSTER_V3_PHASE2_PROMPT.md; docs/LONG_HORIZON_ARCHITECTURE_V3.md.
**Architecture:** One app-owned Qt dispatcher/controller, queued request/cancel signals, async futures on a dedicated mission thread, one process/session workspace lock and an attempt lease. A new content-free controller terminal receipt is emitted only after cleanup. Mission checkpoints retain references, never instructions.

## Constraints and decisions

- Existing GUI defaults, controller limits, worker/capture/provider and stop ownership remain.
- Mission mode forces per-action review; mission review and final human confirmation are separate.
- Only two fixed disposable-editor typing objectives are enabled in the UI; no new planner or arbitrary mission input.
- Session-wide OS file lock plus Qt lease prevent separate Monster instances and manual tasks competing. Unknown cleanup quarantines ownership until process exit.
- Dispatcher consults typed attempt IDs/generations, short-lived authorization and foreground preflight; no QWidget use from mission thread.
- Runtime text is resolved from a volatile fixed objective catalog; no content crosses into checkpoints.
- Reopened real mission runtimes pause for fresh authorization; ambiguous dispatches never replay.

## Tasks / acceptance

1. Baseline and terminal contract: test real SupervisedController closing behavior; add typed content-free post-cleanup receipt without changing existing signals.
2. Lease and queued bridge: test affinity, duplicate/stale callbacks, cancellation, expiry, busy manual/mission and unknown cleanup before implementation.
3. Async runner + durable integration: fake-controller two-subtask test, restart/no replay, timeout/shutdown tests; explicit coordinator flag, preserve fake default.
4. Minimal tray runtime: dedicated async thread, same pill for mission approvals, stop routed both ways, fixed two-editor-sentence demo, restart entry.
5. Full regression/lint/smoke; independent review; native qualification marked pending with exact instructions.

## Review focus

- active=False is not cleanup; no lease release before receipt.
- queued start after cancellation must not submit.
- stale controller callbacks/results cannot complete a new attempt.
- UI shutdown and timeout must resolve futures and preserve uncertainty.
- lost runtime/session ownership and restart must not rearm input.

## Implementation ledger

- Baseline verified:562 passed,3 skipped; Phase 1 prerequisite present at1209e27.
- Tasks1–4 implemented: typed cleanup receipt, shared session lease, queued dispatcher/async futures, real-runner opt-in and revocation on restart, background runtime and tray review.
- Review fixes RED→GREEN: synchronous stop latch before loop/start delivery; disconnected retired listeners and current-runtime UI filtering; loop-retirement shutdown race.
- Ruling: GUI requests use exactly the existing 10/20/300 caps; unsupported stricter envelopes are refused rather than accidentally exceeding a requested cap.
- Ruling: native focus preflight requires the user to focus the disposable editor before passive keyboard mission approval; missing/Monster-owned targets are refused.
- Phase boundary: no strategic planner, arbitrary content persistence, external commitments or unrestricted actions. Native Windows qualification pending.

- Task5 complete: final full suite586 passed,3 native tests skipped in86.03s; Ruff, formatting, frozen sync and offscreen startup passed. Native two-subtask run pending; no Windows reliability claim.
