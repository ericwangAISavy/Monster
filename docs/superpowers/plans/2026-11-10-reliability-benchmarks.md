# Stage 3 reliability benchmarks

> Execute inline, then obtain one independent source review.

**Goal:** Add labelled read-only grounding/verification measurements for the current Ollama Worker, reusing existing region scoring. This implements portable stage 3 tooling; live model measurement and stage 4 Windows qualification remain distinct gates.

**Architecture:** Strict local manifest with hashed bounded PNGs; independent action/status oracles never reach provider prompts. Existing BoundedWorker and selected OllamaProvider produce proposals/assessments only. Explicit recorded replay exercises scoring; opt-in live fixture transmission measures the configured model without changing settings, capturing desktop or dispatching input.

**Constraints:** Work branch only; one existing input executor/ledger unchanged; benchmark has no input/capture/authorization port. No model fallback/download, private screenshots or raw answers/reasoning in reports. Explicit consent for live fixture data. Bounded trials/time/bytes; cancellation and fixed error categories. Synthetic/replay results cannot qualify a model or Windows.

**Review focus:** Oracle leakage, false success/abstention denominators, duplicate-text label versus real duplicate execution, tampered/path-escaped images, replay timing/accuracy misrepresented as model evidence.

- [x] Fail-first tests for exact-target scoring, verification confusion matrix, bounds/privacy/cancellation and routing consent.
- [x] Implement strict manifest, shared Worker runner, metadata-only metrics and CLI; reuse grounding score.
- [x] Build and inspect synthetic editor/search/canvas fixtures with visible text/focus/duplicates/ambiguity; record independent labels and deliberate replay faults.
- [x] Run fixture measurement and applicable/full tests, lint/compile/startup; independent review and fixes.
- [x] Update stage 3/4 status, checkpoint and commands; normal commit/push work without main merge.

Review correction: visible A/B canvas markers and method-specific search/composer
instructions now agree with narrow oracles. Added alignment regression coverage.
Replay: 54/54 trials completed, intentional faulty predictions detected; no model
accuracy, model latency or native input claims. New tests: 24 passed. Full run:
1250 passed / 3 skipped / 3 existing timing failures; affected complete modules
rerun unchanged: 31 passed. A final serial confirmation is being recorded in the
checkpoint. No production controller, executor, ledger or permissions changed.

Final confirmation: 1253 passed, 3 skipped in 307.29s; lint/format/compile/startup/diff checks passed. Commit and normal push target is work only.
