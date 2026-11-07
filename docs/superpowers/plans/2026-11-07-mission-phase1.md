# Durable mission Phase 1 implementation plan

**Goal:** Add persistent fake-only mission orchestration above the existing GUI engine.
**Architecture:** Async LangGraph, AsyncSqliteSaver and a separate synchronous SQLite effect ledger. JSON-only checkpoints contain bounded metadata and opaque objective/evidence references, never desktop content or executable actions. A single runtime owns each database; recovered dispatch intent cannot dispatch again.
**Spec:** docs/LONG_HORIZON_ARCHITECTURE_V3.md and the user-supplied Phase 1 prompt.

## Global constraints

- Phase 1 only: fake runner, no Qt integration, input, provider calls or GUI changes.
- Existing controller budgets, approvals and independent stop ownership remain intact.
- Approvals bind mission/subtask/effect/generation/deadline; do not carry commands.
- Checkpoints are progress records, never proof of an application side effect.

## Review focus

- Crash at ledger/checkpoint boundaries: no duplicate dispatch.
- Late/stale/denied approval: no dispatch.
- Concurrent runtimes and stop during hung runner: fail closed, reject stale results.
- Missing/malformed evidence: never complete optimistically.
- Migration/retention: reject future schemas; preserve unresolved effects.

## Tasks

1. Contracts and store: write failing validation, transition, migration, retention tests; implement versioned strict contracts, content-free ledger and runtime ownership.
2. Graph and runner: write failing three-subtask approval/restart and dispatch-crash tests; implement fake async runner and bounded graph transitions with durable interrupts.
3. Recovery: write failing cancellation, timeout, malformed result, stale approvals and cross-process restart tests; implement reconciliation, stop flags and budgets.
4. Verify: run full existing suite, lint, frozen sync and startup smoke; review architecture boundaries; document exact evidence and Windows gaps.

## Decisions

- User explicitly supplied the design and requested implementation; execute inline without another design approval round.
- Reference-only content is deliberate: Phase 1 accepts opaque objective/criteria IDs rather than persisting arbitrary personal instructions. A future adapter resolves content outside checkpoints.
- SQLite checkpoints and effect ledger use separate connections/files. Effect ledger commits before runner delegation; restart conservatively pauses any dispatch without a durable observed outcome.
- No automatic retry/replan of failed GUI effects in Phase 1. Recovery returns partial/paused; replanning seam remains bounded and explicit.

## Completion ledger

- Tasks 1–3 complete: contracts/store and async graph/fake runner implemented; 42 new tests cover all acceptance cases, including separate-process restart.
- Task 4 complete: full suite 562 passed / 3 native Windows skipped; Ruff, formatting, frozen sync and offscreen startup passed. Exact commands in `docs/MISSION_PHASE1_IMPLEMENTATION.md`.
- Review fixes: atomic DDL migration, monotonic cancellation, idle cancellation retention, pre-await operation reservation; all reproduced RED→GREEN.
- Legacy state migration is applied at node entry and routing before approval resumption.
- Ruling: preserve reference-only checkpoint contents and reject non-fake adapters — Phase 2 must separately resolve content and bridge Qt; this intentionally leaves natural-language GUI missions unavailable in Phase 1.
