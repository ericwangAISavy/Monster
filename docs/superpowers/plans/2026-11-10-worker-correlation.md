# Consistent Worker correlation implementation plan

> **For agentic workers:** Execute inline using superpowers:executing-plans, then request independent review.

**Goal:** Correlate proposal, action-verification and scene-comparison requests/results as strictly as the existing visual checks.

**Architecture:** Reuse versioned reference-only bindings and frozen RAM packets. Immutable proposal snapshots preserve legacy Decision fields including coordinate provenance; verified action assessments remain advisory. Scene comparison binds both observations and the exact image action. Host job/config/phase/task/frame/generation checks precede legacy interpretation and all input remains behind the same durable authority.

**Spec:** docs/WORKSPACE_CHECKPOINT.md and V4_BOUNDED_WORKER.md; user authorizes the next recommended slice on work.

**Constraints:** One ProviderJobs thread, one input executor, same ledger, no new scheduler/schema/permissions, native and guest qualification unchanged. Preserve capture-only decisions, routine Enter/click, review/hands-free, fresh scenes, all drag endpoints, cancellation, receipts/cleanup and no ambiguous replay.

## Approach

Extend the common binding rather than introduce a second correlation system. Keep the existing provider-neutral Decision/Verification projections at the host boundary; freeze private payloads inside envelopes and exclude them from exports. No broader lifecycle rewrite or compatibility-probe migration in this slice.

## Review focus

Raw or wrongly correlated results must not dispatch. Proposal payload mutation must not alter a reviewed snapshot. Verification must match the originally dispatched action/criterion. Scene match alone is not input authority. Errors/cancellation must retain cleanup and uncertainty without leaking raw content.

## Tasks

- [x] Add failing tests for typed outcomes, private immutable snapshots and two-frame/action bindings.
- [x] Implement shared bindings and typed request/result packets, reusing existing contracts and validation.
- [x] Add host refusal tests and route all operational Worker replies through one correlation gate, preserving existing host interpretation.
- [x] Run focused and full frozen regressions, lint/compile/startup/diff checks, independent review; repair substantive findings with regression evidence.
- [x] Update checkpoint/boundary docs; normal commit and push to work, no merge to main.

## Verification ledger and rulings

Baseline: d89734f, prior full suite 1,202 passed / 3 skipped. New binding tests
failed before implementation, then passed. A capture-only scenario reproduced
false completion failure despite an observed_success ledger row: finish_attempt
returns all retained records. Querying the shared unresolved predicate repairs the
actual classification without changing journal history or cleanup requirements.

Initial focused/serial attempts exposed legacy done projection rejection and a
stale prebuilt drawing capture; failed mission assertions also preceded a native
Qt crash in test teardown. The legacy alias is preserved without changing the
provider parser/permissions, and the fixture now timestamps actual simulated
captures freshly. No production freshness threshold, task limit or cleanup barrier
was increased. Grounding deadline passed in the isolated and final runs.

Independent source review found no critical/important issues, confirmed the
journal predicate and suggested explicit pending-action coverage; both new cases
were added. Mission/drawing/grounding regressions: 37 passed. Final serial frozen
full suite: 1,229 passed, 3 skipped in 397.96s, including all 27 new cases.
Lint, formatting, compilation, startup smoke and diff checks passed.

Ruling: fix the directly reproduced standalone finalization defect in this slice
because reliable end-to-end correlation cannot be proven while resolved history
is misclassified as uncertainty. Reuse the shared unresolved query; do not weaken
or rewrite effect states. Native/model qualification remains pending.

User delivery selection remains normal push to work, no merge to main.
