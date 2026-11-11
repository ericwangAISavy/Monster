# Bound compatibility probe implementation plan

User authorized the next implementation inline. Native Windows qualification is
still pending. This slice repairs the existing read-only compatibility boundary,
without enabling step 5 execution or changing any input/ledger/review policy.

**Design:** The operational controller currently advances after any error-free
probe object; its queued closure also reads mutable controller configuration.
Snapshot the existing frozen OllamaConfig before queuing. Add a stateless Worker
probe operation with a typed nonce/task/generation/model/config binding, no desktop
frame. Validate exact ProbeResult model and positive boolean vision/JSON checks.
Bind the outcome back to the current controller request before any capture. Keep
provider-native probing, diagnostics, deadlines, cancellation and settings-dialog
checks intact; no retries, settings changes or new model selection.

**Alternatives:** Merely checking truthiness leaves stale/mismatched results;
a new qualification service would duplicate existing boundaries. Reuse
BoundedWorker/ProviderJobs with a small no-frame probe contract.

**Files:** `agent/worker_probe.py`, `agent/worker.py`, `agent/controller.py`,
`tests/test_worker_probe.py`, checkpoint/Worker documentation.

- [x] RED tests: positive bound metadata/privacy; negative/malformed/wrong-model
  results; exact frozen config/request binding; cancelled operation; controller
  wrong nonce/raw result/lost request/changed config/generation/task/phase stop
  before capture; valid probe preserves normal capture and foreground behavior.
- [x] Implement immutable request/result and existing Worker delegation; bind the
  controller using its existing job/request slot and fail-closed result checks.
- [x] Run focused controller/probe/worker regressions; independent source review
  and fail-first repairs. Run full serial suite, lint, compile, startup/diff checks.
- [x] Update checkpoint with actual results/limitations; commit and push work
  normally. No merge main or execution gate bypass.

Evidence ledger: baseline 0980901. Initial RED: 23 failures (missing probe
contracts/validation). Corrected test cleanup method from shutdown to close.
Queue-specific RED with old closure: provider constructed with changed config;
restored exact submitted snapshot. GREEN: 251 focused regressions in 41.96s,
including 24 new cases. Source review: no critical/important findings; minor
(deferred): direct forged result-constructor/invalid-field revalidation cases.
No production timeout, provider schema, execution policy or qualification changes.

Final verification: 1336 passed, 3 skipped in 169.11s; setup/lint/compile/format/startup/diff checks passed. User-authorized normal commit/push to work; main untouched.
