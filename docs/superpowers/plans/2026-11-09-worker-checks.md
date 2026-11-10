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

- [ ] Write failing Worker tests for each explicit operation, immutable action snapshots, result schema/status/privacy and cancellation.
- [ ] Implement typed visual-check request/binding/result and one-shot Worker dispatch with explicit allowlisted operations.
- [ ] Write host binding-refusal tests, then delegate draft/canvas/launch/completion checks through the same ProviderJobs thread and configuration binding.
- [ ] Run controller, hands-free, drawing, app-launch and durable authorization regressions; fix reproducible failures.
- [ ] Run full frozen suite, lint/compile/startup/diff checks; independent review and meaningful regression fixes.
- [ ] Update checkpoint and boundary docs; commit and push work normally without merging main.

User authorized this concrete next incremental slice. No additional design approval round is required.
