# v3 Phase 2 — safe mission / GUI integration

**Implemented, native Windows qualification pending.** This is an explicit
experimental adapter to the existing supervised engine, not unrestricted autonomy.
The default mission runner is still fake. No provider, input worker, capture loop,
character, saved inference settings or default GUI mode was replaced.

## Execution and contract map

| Layer/event | Phase 2 binding |
| --- | --- |
| `MissionRuntime` | A dedicated asyncio thread owns Coordinator, LangGraph and SQLite. Qt receives metadata updates only through signals. |
| `GuiSubtaskRequest.effect_id` | Stable attempt/idempotency key from the durable Phase 1 ledger. Request carries mission, subtask, workspace, generation, caps and authorization expiry; no instruction or action dictionary. |
| `AsyncGuiSubtaskRunner` | Creates a loop-owned future, emits a queued handoff, resolves via `loop.call_soon_threadsafe`. Cancellation latches before requesting Qt stop; bounded cleanup wait. |
| `MissionGuiBridge` | Qt-owned dispatcher holding the **existing app controller**. Resolves two fixed objective references in volatile memory, runs foreground/session preflight, forces per-action review for that run, and calls `submit` once. |
| Controller generation | Captured at accepted submit. `ExecutionReport.run_generation` must match; duplicate attempt IDs and stale reports are rejected. |
| Controller `active_changed(False)` | Informational only. Never releases ownership or establishes success. |
| `terminal_ready(ExecutionReport)` | New content-free receipt emitted only from the existing closing/cleanup result path, after worker cleanup is known. Existing `finished`/`failed` signals retain their behavior. |
| `TaskResult` | Verified success requires visual success, explicit final human confirmation, correct generation and confirmed cleanup. Includes observation reference, transaction count and actual configured model identity (hashed if not representable by the bounded ID contract). |
| `WorkspaceGate` | Scheduling lease plus session-scoped OS file lock. Manual supervised tasks and mission subtasks share it; another Monster process cannot acquire it. Unconfirmed cleanup permanently quarantines that gate until process exit. |

The scheduling lock is distinct from the worker's physical input lease. It is held
across one bounded subtask, including its action reviews, to prevent a second
Monster writer. The existing worker arms only for an approved transaction and
then disarms. Mission-level review happens **before** acquiring desktop ownership.
No input authority is retained across arbitrary outer mission review waits.

All QObject/controller methods and UI handling run on the Qt affinity thread.
Cross-thread code emits signals or uses thread-safe future/event primitives; it
never calls controller `submit/respond/cancel/close` directly. One dispatcher
connects once, retires its listeners and deletes its Qt objects on shutdown.
Old-runtime UI updates are identity-filtered. Stop synchronously blocks queued
starts on Qt before scheduling durable cancellation. The independent Win32 stop
hotkey and existing worker cleanup path remain authoritative for actual input.

## Caps, authorization and scopes

Mission Mode currently supports only the two disposable-editor objective references
in `runtime.EDITOR_OBJECTIVES`. Requests must target `interactive-desktop`, use the
existing 10-input / 20-planning / 300-second envelope, be local-reversible/read-only,
and require explicit subtask review. Other envelopes are refused rather than
silently enlarged. No arbitrary planner, terminal objective, external submission,
account action or command execution is enabled.

Three distinct approvals remain: graph subtask grant, each controller proposal,
and final visible-result confirmation. Mission Mode temporarily forces
`approve_each_step=True` on the existing controller and restores its old config
only when that attempt ends; saved preferences are untouched. Denial/Escape/expiry
or user Stop prevents further delegation. Each subtask begins with new session,
foreground and screenshot preflight through the existing app capture isolation.
For mission review, focus the disposable editor and use **Ctrl+Alt+Y** so the
approval gesture does not focus Monster. A missing target is refused, not guessed.

## Durability and restart

Phase 1 commits `prepared → approved → dispatch_started` before a request can
reach Qt. The input-intent marker does **not** mean native input occurred. The
adapter's typed terminal receipt supplies the observed outcome, and it commits
before another subtask is selected. Unknown results pause as uncertain; no blind
repeat or new mouse coordinates are checkpointed.

A real-runtime reopen revokes old undispatched grants, increments the mission
control generation, and resets only **proven undispatched** prepared/approved
records. It never resets a dispatch-started/uncertain effect. Explicit
`continue_mission` schedules fresh preflight/approval for eligible remaining work;
old resume tokens fail. Verified subtasks are never rerun. Ambiguous dispatch is
reconciled through the existing evidence-based API or handed off for inspection;
there is no automatic replay or deeper reconciliation UI in this phase.

The version-1 contracts gained defaulted terminal-evidence and authorization-expiry
fields. Old JSON records still load, and old successes are not retroactively
claimed to have native cleanup proof. No ledger schema rewrite or new dependency
was needed; the locked LangGraph APIs from Phase 1 are reused.

Checkpoints contain reference IDs, flags, statuses and numeric metadata. The fixed
instructions resolve on Qt and do not enter graph state. Existing content-bearing
single-task diagnostics remain in RAM unless the user explicitly exports them.
No screenshot/password/token/raw model reasoning is added to persistent state.
SQLite remains unencrypted; use private local storage and normal OS protection.

## Entry points and shutdown

1. Configure a working model and choose existing **Real computer control**.
2. Tray → **Experimental Mission · two Notepad drafts…**. Nothing starts in other
   modes or on non-Windows hosts. Each subtask receives a mission review pill.
3. Tray → **Resume latest experimental mission…** deliberately opens the latest
   unfinished non-cancelled mission from the private `missions/` config subdirectory.
4. Stop/Pause/Escape invalidates the mission; changing settings also stops it.
   Active ownership prevents controller replacement or another manual task.
5. UI shutdown latches cancellation, stops the existing controller and resolves
   pending runner futures uncertain. Its worker closes on its own thread; Qt is
   never blocked waiting for graph/SQLite. Unknown cleanup keeps the gate closed.

## Validation evidence

Pre-change baseline: **562 passed, 3 skipped**, full pytest in 91.73 seconds.
Initial integrated suite: **575 passed, 3 skipped**, 84.40 seconds.
Subsequent review reproduced and fixed stop-before-queued-start and retired-runtime
listener/UI races. Final counts and exact commands are recorded below. All new integration tests use fake controllers/native providers;
none establish actual Windows input or LAN model reliability.

Test coverage includes two sequential checkpointed subtasks through the real
queued bridge, Qt affinity, duplicate/stale reports, cancellation and cleanup
wait, shutdown, refusal during manual ownership, cross-gate file locking, approval
expiry/workspace mismatch, fresh authorization after restart, watchdog quarantine,
actual model identity, volatile content exclusion, and existing Phase 1 crash tests.

## Remaining limits / Phase 3 proposal

Native Windows two-subtask execution, focus restoration, actual stop latency,
held-input cleanup and renderer visibility are **pending**, using
[the qualification procedure](WINDOWS_PHASE2_QUALIFICATION.md). Offscreen tests
prove software routing only. Keep a human present. Controller/OS stalls cannot
provide a hard real-time stop guarantee; use the independent hotkey and inspect
held input whenever cleanup is unknown.

Proposed Phase 3 scope: durable per-transaction receipts linked to subtask effect
intent, a human reconciliation surface for uncertain outcomes, and deterministic
consequence-aware authorization scopes. Keep external sends/deletion/credentials
out until that policy and crash-reconciliation gate is qualified. Do not add a
strategic planner, new providers or unattended input as part of that work.

### Exact verification commands

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run pytest -q
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff check src tests
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff format --check src/monster/mission src/monster/agent/terminal.py tests/mission/test_gui_adapter.py
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run monster --smoke-test
git diff --check
```

Startup exited 0 with the expected offscreen tray/raise messages. Frozen sync and
lint passed; no dependency/lockfile change was needed. The intermediate final run
passed **585 tests, 3 skipped in 87.76 seconds**. A subsequent loop-already-closed
shutdown regression was reproduced and fixed before release; see the final count
below. Tests that depend on asyncio thread wakeups ran outside the restricted
command sandbox using the ordinary execution approval mechanism.

**Final release run: 586 passed, 3 skipped in 86.03 seconds** (24 new tests over
the Phase 1 baseline). Ruff and the listed formatting check passed, with no test
warnings in the final run. Native Windows/manual qualification remains pending.
