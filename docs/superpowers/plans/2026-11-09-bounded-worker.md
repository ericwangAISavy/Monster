# Bounded Worker extraction implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement inline, then request one independent review.

**Goal:** Extract a small read-only operational Worker from the existing controller without moving execution authority.

**Architecture:** `agent/worker.py` performs one proposal/verification call, one scene comparison or one grounding/projection operation at a time, using immutable RAM request snapshots and existing operational contracts. It has no input, journal, scheduler, retries, Qt lifecycle or grant port. Controller policy/admission, target checks, duplicates, limits, generations and cleanup retain their existing owners. Existing ProviderJobs supplies the same thread and cancellation boundary.

**Spec:** `docs/WORKSPACE_CHECKPOINT.md`, `docs/V4_OPERATIONAL_CONTRACTS.md`, `docs/V4_DURABLE_AUTHORIZATION.md`, and the user's bounded extraction request.

**Tech Stack:** Existing Python/PySide6/provider jobs and operational contracts; no dependencies/schema changes.

## Global constraints

Keep one input executor and shared effect ledger. Commit intent before input. Preserve hands-free, per-step review, routine Enter/click, fresh scene/foreground/display, draft/canvas, cancellation, cleanup and explicit uncertain reconciliation. No guest-native enabling or real Windows/model claims. Work branch only.

## Design choices

A full inner-loop extraction would move many lifecycle transitions and make safety review harder. A new threaded/queued Worker would duplicate ProviderJobs ownership. Choose a stateless Worker with bounded inference/grounding operations; leave probe, target/completion state transitions and result interpretation in Qt for a later slice. User explicitly authorized choosing and completing an incremental extraction, so no separate design approval round is needed.

## Review focus

Queued inference must use the submitted model/config/action snapshot. Changed drag endpoints or generation must fail contract binding. Worker completion is advisory, never a grant. Late replies must not dispatch after cancellation. Failed intent or finalization must retain uncertainty and prevent optimistic completion.

## Task 1 — Worker and read-only packets

- [x] Write and run failing `tests/test_bounded_worker.py` cases for isolated provider calls, snapshots, cancellation and physical grounding/contract refusal.
- [x] Implement immutable `VisualContext`, `ProposalRequest`, `VerificationRequest`, and stateless `BoundedWorker`; reuse existing validation/adapters rather than another policy.
- [x] Verify direct worker tests.

## Task 2 — Controller delegation

- [x] Test controller uses Worker, snapshots queued model settings, and cannot convert a Worker result into direct input before durable intent.
- [x] Route existing proposal, action verification, scene comparison, preparation and contract projection through Worker. Preserve all host transitions, journals, dispatch and cleanup.
- [x] Run actual standalone/mission authorization, hands-free, draft/canvas and controller regressions with fake input and SQLite.

## Task 3 — Delivery

- [x] Run frozen full tests, lint, compile, smoke and diff checks.
- [x] Independent review; add regression tests for material findings and repair them.
- [x] Update architecture notes/checkpoint with precise scope, evidence and next slice.
- [x] Commit/push work normally; preserve remote concurrent work and do not merge main.

## Execution evidence

Baseline: 1,155 passed, 3 skipped in 149.49s. Initial Worker tests failed because
the extraction was absent, then passed. Queued configuration regression reproduced
selection of `changed-while-queued`; captured requests and returned-config validation
repair it without new input. Worker suite: 12 passed. Final full frozen suite:
1,167 passed, 3 skipped in 156.21s. Lint, compileall, smoke, new-file formatting
and diff checks passed. Independent source review reported no critical/important
findings.

Ruling: extract one-shot read-only operations now, retaining target/completion
interpretation and lifecycle in Qt — the user authorized a concrete incremental
slice; moving the full state machine would increase authorization regression risk.
Remaining native/model qualification is unchanged.
