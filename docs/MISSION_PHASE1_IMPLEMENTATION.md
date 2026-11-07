# Monster v3 Phase 1 — implementation evidence

Implemented November 7, 2026. This is an offline orchestration milestone, **not**
Windows or long-horizon autonomy qualification.

## Changes

- Added `src/monster/mission/`: strict versioned metadata contracts, async LangGraph
  workflow, persistent SQLite checkpoints (`thread_id=mission_id`), durable effect
  ledger, bounded authorization interrupts, generation invalidation and explicit recovery.
- Added fake GUI runner and two runnable offline demonstrations. The coordinator
  rejects non-fake runners; it does not import the GUI, controller, provider or input worker.
- Added 42 mission tests. They cover routing/dependencies, durable approval and
  restart, hard process exit after dispatch, every effect boundary, observed-result
  recovery, denial/expiry, timeout/error/malformed/unbound results, cancellation,
  concurrent approvals, runtime ownership, atomic schema migration, legacy checkpoint
  migration, retention and privacy with network connections blocked.
- Added adopted v3 blueprint with Phase 1 status, integration seam and limitations;
  README link and local demo-state ignore convention. Existing GUI/controller/tools
  and defaults were not modified.
- Locked actual installed APIs: LangGraph **1.2.20**, SQLite checkpointer **3.1.3**,
  Pydantic **2.14.3**. Inspected `AsyncSqliteSaver.from_conn_string`, persistent
  state lookup, `adelete_thread`, interrupt/Command resume and sync durability APIs.

## Actual verification

Linux, Python 3.12, Qt offscreen. Commands run from the repository root:

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run pytest -q
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff check src tests
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff format --check src/monster/mission tests/mission
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run monster --smoke-test
UV_CACHE_DIR=/workspace/.cache/uv uv run python -m monster.mission.demo --directory /tmp/monster-v3-demo-approval --scenario approval
UV_CACHE_DIR=/workspace/.cache/uv uv run python -m monster.mission.demo --directory /tmp/monster-v3-demo-crash --scenario crash
git diff --check
```

Final complete suite: **562 passed, 3 skipped in 78.83 seconds**. Baseline was
520 passing tests; all 42 additional mission tests passed. Ruff, formatting check,
frozen sync and diff checks passed. Startup exited 0; offscreen Qt reported expected
unsupported tray/raise messages. No visual Windows claim is inferred from startup.

The sandbox prevented asyncio SQLite thread wakeups; the same tests were rerun
outside that sandbox using the normal tool approval mechanism. An initial full
run found a missing `json` import in the new demo test (559 passed, 1 failed,
3 skipped); the import was corrected and the full command rerun successfully.
No application failure was hidden by that test correction.

Demonstration results:

| Scenario | Before reopen | After reopen | Total fake dispatches | Synthetic verified | Uncertain |
| --- | --- | --- | --- | --- | --- |
| Approval | waiting_approval (two done) | completed | 3 | 3 | 0 |
| Dispatch crash | dispatch_started | paused/reconciliation_required | 1 | 0 | 1 |

Subprocess tests additionally exercise an actual process restart for the three-task
approval scenario and `os._exit(73)` after dispatch intent commits. The latter
reopens paused and never calls the new runner. These are synthetic effects only.
A privacy subprocess enables global tracing settings, prohibits network connections,
and checks that malformed sensitive fields are absent from SQLite files.

An independent code review identified non-atomic DDL migrations, non-monotonic
cancellation and missing terminal retention markers for idle cancellation. Each
was reproduced with a failing regression test, fixed, and included in the final
passing suite. Another regression reserves mission operations before their first
await, preventing overlapping approval/checkpoint mutation.

## Remaining limitations

- No real GUI adapter, model planner, model routing change or autonomous input.
  Static validated plans only; failed/uncertain work does not automatically replan.
- Only opaque non-sensitive objective/evidence references are persisted. Arbitrary
  personal task text and raw evidence need a separately reviewed transient or
  encrypted content design. SQLite itself is not encrypted; use private local
  storage and OS access controls. Caller must never encode secrets in identifiers.
- Single runtime per local state directory; no distributed scheduling, cloud sync,
  dedicated Windows workspace or desktop lease. Do not use a network filesystem.
- A manual stop invalidates old authorization. An ambiguous result needs explicit
  evidence-based reconciliation; an invalidated approval cannot silently renew.
- The three skipped tests require native Windows/UIAccess. Windows foreground,
  screenshot isolation, held-input cleanup, signed renderer and above-Start behavior
  have not been requalified here. No LAN model endpoint was contacted.
- No new GUI entry point is enabled; normal `uv run monster` keeps existing behavior.

## Recommended Phase 2

Implement one Qt-thread-safe runner adapter using signals/futures, cancellation and
stale-generation rejection. Keep a single workspace/input owner, reuse the existing
supervised controller and independent stop worker, resolve content outside graph
state, and require worker cleanup plus fresh visible evidence before returning a
verified result. Add adapter race/crash tests and native Windows qualification for
two harmless sequential subtasks before permitting opt-in real mission delegation.
Retain all existing transaction budgets and consequence reviews. Strategic model
planning and unrestricted autonomy remain outside that PR.
