# Bounded visual-check requests implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; request independent review before delivery.

**Goal:** Move remaining operational target/completion checks behind the existing stateless Worker, with strictly bound advisory outcomes.

**Architecture:** Frozen RAM requests reuse existing ObservationFrame/ActionPayload contracts. Strict metadata-only result envelopes bind nonce, task, operation, generation, model and exact observation. The host checks those bindings, current phase/configuration and observation before interpreting readiness/completion; admission, intent, input and cleanup retain their existing owners.

**Spec:** User's next-step authorization; docs/WORKSPACE_CHECKPOINT.md and V4_BOUNDED_WORKER.md.

**Tech stack:** Existing Python/PySide6/ProviderJobs/Pydantic; no new dependencies, scheduler, ledger or thread.

## Constraints and choices

Keep one executor and shared durable authority. No permissions/guest-native expansion. Keep hands-free, Enter/click, exact review, scene/canvas/draft checks and cancellation. Requests/criteria/images stay RAM-only. Probe remains a provider compatibility operation; task interpretation and completion remain host decisions. Choose typed outcomes for the four newly extracted checks in this increment, preserving existing proposal/action-verification contracts.

## Review focus

Copied request IDs with changed frame/model/generation must fail. Ready/succeeded in the wrong operation must fail. Provider errors must not leak private evidence. Cancellation must invalidate late checks. Completion assessment must not finalize before cleanup/journal barriers.

## Steps

- [x] Write failing Worker tests for each explicit operation, immutable action snapshots, result schema/status/privacy and cancellation.
- [x] Implement typed visual-check request/binding/result and one-shot Worker dispatch with explicit allowlisted operations.
- [x] Write host binding-refusal tests, then delegate draft/canvas/launch/completion checks through the same ProviderJobs thread and configuration binding.
- [x] Run controller, hands-free, drawing, app-launch and durable authorization regressions; fix reproducible failures.
- [x] Run full frozen suite, lint/compile/startup/diff checks; independent review and meaningful regression fixes.
- [x] Update checkpoint and boundary docs; commit and push work normally without merging main.

User authorized this concrete next incremental slice. No additional design approval round is required.

## Verification ledger

Baseline: d0773b1, 1,167 passed / 3 skipped. New operations first failed because
VisualCheckRequest/check were absent, then passed. Host lost-binding regression
reproduced actual fake text input before the refusal gate and now yields zero
intent/input. Independent review reported no critical/important findings; its
constructor consistency observation was fixed with a failing-then-passing strict
type regression. New checks: 35 passed. Focused controller/launch/drawing/hands-free/
standalone/mission-dispatch tests: 286 passed. Final frozen suite after all fixes:
1,202 passed, 3 skipped in 161.57s. Frozen sync, lint, formatting, compileall,
startup smoke and diff checks passed. Native/model qualification remains pending.

No new scheduler, executor, ledger, schema migration or permission was introduced.
The user's standing delivery instruction selects a normal push to work without
a merge to main. Code commit: fb60812; checkpoint validation saved in the
following documentation commit.
