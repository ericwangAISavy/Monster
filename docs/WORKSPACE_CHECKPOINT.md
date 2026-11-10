# Monster workspace checkpoint

This handoff is saved in Git so another workspace can resume without the original
chat, uploaded files, local caches, or cloud environment instance.

## Resume point

- Repository: `https://github.com/ericwangAISavy/Monster.git`
- Development branch: `work` (do not assume these changes are on `main`).
- Prior implementation baseline: `e4dafcf` — Journal standalone input before native dispatch.
- Prior checkpoint: `d3c5eaf`; first Worker extraction: `d0773b1`.
- Current implementation: `fb60812` — bound visual-check Worker follow-up,
  on `work`; see [the extraction boundary](V4_BOUNDED_WORKER.md).
- At checkpoint creation, the implementation was committed and pushed, with no
  unfinished code changes. No merge into `main` was performed.

In a fresh workspace, clone the repository and select `work`. If already checked
out, first inspect the branch and working tree; preserve any local changes before
switching or updating. Fetch the remote and confirm `e4dafcf` is an ancestor of the
selected branch. Do not reset or overwrite another workspace's changes.

```bash
git clone --branch work https://github.com/ericwangAISavy/Monster.git
cd Monster
git status --short
git log -4 --oneline
git merge-base --is-ancestor e4dafcf HEAD
```

GitHub HTTPS authentication was supplied by the cloud platform. Both
`git ls-remote origin HEAD` and an actual push to `origin/work` succeeded after the
environment recovered. No personal access token or credential change was needed.
A replacement workspace must use its own supported authentication; this checkpoint
contains no credentials or machine-specific Git configuration.

## User goal and current scope

Monster is a Windows desktop agent using screenshots, mouse and keyboard. The
user reported repeated mid-task stops, especially uncertain visual verification
and searching for Paint/Notepad without pressing Enter. They also requested a
right-side sticky control center and routine Enter/click automation without
additional prompts. Those reliability/UI changes preceded the current V4 migration.

The current work incrementally implements the reviewed V4 architecture. This is
not a claim that V4, real-model reliability, or native Windows qualification is
complete. The original architecture and planning prompts arrived as chat uploads;
new workspaces should not depend on their old attachment paths. The implementation
state and decisions needed for the next slice are recorded below and in the linked
repository documents. Revisit the original documents with the user if later work
requires requirements beyond this recorded scope.

## Completed V4 slices

1. **Risk vocabulary boundary:** canonical provider `routine` is mapped explicitly
   into the existing mission policy vocabulary; sensitive/uncertain hints cannot
   lower deterministic risk. Mission policy was not broadened.
2. **Operational contracts:** immutable/versioned proposals, observations, mapped
   actions, receipts and verification records. Metadata exports exclude executable
   payloads and raw criteria. Unknown native DPI/session/worker metadata remains
   unknown rather than fabricated. See [operational contracts](V4_OPERATIONAL_CONTRACTS.md).
3. **Mission durable binding:** mission effect version 3 binds exact proposals,
   controller generation and fresh mapped actions to existing exact approvals.
   Approval consumption and intent commit before input. Commit `6dc9e48`.
4. **Review policy extraction:** host-selected automatic/review/stop admission
   preserves existing per-step and hands-free behavior. Commit `a86e6f6`.
5. **Standalone durable dispatch:** commit `e4dafcf` connects application standalone
   tasks to the same ledger and effect coordinator. See
   [durable authorization](V4_DURABLE_AUTHORIZATION.md), especially slice 2b.
6. **Bounded Worker extraction:** `agent/worker.py` now owns one-shot proposal,
   action-verification, scene-comparison, physical-preparation and contract-projection
   operations. The controller retains lifecycle, reviews, durable intent, dispatch,
   result interpretation and cleanup. Queued requests pin model/configuration and
   reject changed host settings. See [slice 2c](V4_BOUNDED_WORKER.md).
7. **Bound target/completion checks:** slice 2d moves draft/canvas readiness,
   app-launch observation and whole-task visual inference into one-shot Worker
   checks. Strict advisory outcomes bind the exact request, task, operation, model,
   captured frame and generation. Host checks configuration, phase and current
   observation; missing/forged bindings stop before input or completion.

Standalone uses one serialized metadata worker and the existing repository's
exclusive `runtime.lock`; it does not introduce another database, scheduler or
input executor. Host sessions pin task identity, generation, instruction and mode.
Exact reviews bind proposal digests and request references. Automatic admission
never creates a human-approval record. Original decision risk aliases are checked
so canonical normalization cannot weaken the original review policy.

Standalone effect version 4 distinguishes `standalone_automatic` from
`standalone_human`. SQL schema remains 6; existing action-effect/audit tables are
reused. Mission version 3 effects and version 2 exact approvals remain unchanged.
Standalone does not synthesize mission rows or mission approvals. Raw payloads,
screenshots, instructions and criteria are not written to the ledger.

Dispatch waits for committed intent, then checks the current lease, generation,
reviewed proposal and observation age again. Input receipts and final visual
observations use the shared coordinator. Completion and desktop-lease release
wait for both input cleanup and journal finalization. Write failures cannot yield
optimistic completion. Possibly dispatched effects survive restart as uncertain,
block further input and require the existing reconciliation flow; no blind replay.
A general task-completion confirmation does not implicitly reconcile an uncertain
effect. The **Review uncertain effects** control is the explicit handoff.

## Code map

| Area | Entry points |
| --- | --- |
| Qt app, manual workspace lease, runtime switching | `src/monster/app.py` |
| Qt lifecycle, authorization and input handoff | `src/monster/agent/controller.py` |
| Bounded read-only inference, comparison and grounding | `src/monster/agent/worker.py` |
| Bound target/completion requests and advisory outcomes | `src/monster/agent/worker_checks.py` |
| Host review policy | `src/monster/agent/admission.py`, `validation.py` |
| Standalone serialized journal lifecycle | `src/monster/agent/standalone_journal.py` |
| Immutable operational contracts and legacy adapters | `src/monster/agent/contracts.py`, `contract_adapters.py` |
| Shared durable intent, receipts, recovery, attestation | `src/monster/mission/effects.py` |
| Versioned effect/approval storage records | `src/monster/mission/contracts.py` |
| SQLite schema, WAL/FULL durability, exclusive runtime lock | `src/monster/mission/repository.py` |
| Mission orchestration and Qt dispatch bridge | `src/monster/mission/coordinator.py`, `runtime.py`, `gui_bridge.py`, `gui_subtask_adapter.py` |
| Session-wide OS workspace lease and quarantine | `src/monster/mission/workspace.py` |
| Input bridge and physical worker boundary | `src/monster/agent/input_bridge.py`, `src/monster/computer/` |

## Next recommended implementation

The first Worker extraction and its **bound target/completion check** follow-up
are implemented. The next small slice is consistent typed request/result
correlation for existing proposal, action-verification and scene-comparison Worker
operations. Those still use legacy Decision/Verification/bool returns under the
host job/configuration guards. Reuse the new advisory envelope pattern without
replacing operational proposals, exact grants, effect receipts or the journal.

The Worker remains stateless and read-only; the controller is the Qt lifecycle/UI
adapter and LangGraph remains the outer orchestrator. Probe remains a controller
compatibility operation; task interpretation, history/limits and all execution
authority remain outside the Worker. No new worker thread or executor was added.

Preserve these invariants during the extraction:

- Host policy and durable admission remain distinct from model proposals and
  visual assessments. Model evidence alone does not create execution authority.
- Preserve routine Enter/click behavior and current review/hands-free settings;
  hands-free stops on review-required actions rather than adding prompts.
- Preserve scene freshness, foreground/display binding, both drag endpoints,
  draft/canvas checks, cancellation/generation checks and the cleanup barrier.
- Keep durable intent before every application dispatch, including standalone.
  Test failed writes, changed bindings, stale replies and cancellation with queued
  intent using fake input and the actual shared SQLite service.
- Keep metadata-only durable storage and the exclusive ledger/workspace leases.
  Never reset uncertainty or weaken duplicate-action protection to make tests pass.
- Retain screenshots/mouse/keyboard interaction. Do not add DOM, app APIs,
  clipboard automation or shell-command execution as a shortcut.

Later work includes grounding/verification benchmarks and Windows qualification,
then separately qualified interactive orchestration, provider/egress credentials
and declarative skills. Guest-native execution remains blocked by ADR006 transport
and qualification prerequisites; do not bypass the simulation-only guest boundary.

## Development environment and verification

Use the existing checkout; a new cloud task is already isolated. No Git worktree
is needed unless the user asks. Read any applicable `AGENTS.md` in the new workspace.
None was found in the original repository/parent inspection. Setup does not need
an Ollama server, cloud API key or native input permission for offline tests.

Python 3.12+ and `uv` are required. On Linux:

```bash
export UV_CACHE_DIR=/workspace/.cache/uv
export XDG_CACHE_HOME=/workspace/.cache
export XDG_CONFIG_HOME=/workspace/.config
export QT_QPA_PLATFORM=offscreen
mkdir -p "$UV_CACHE_DIR" "$XDG_CONFIG_HOME"
uv sync --frozen
uv run --frozen ruff check src tests
uv run --frozen pytest -q
python -m compileall -q src
uv run --frozen monster --smoke-test
git diff --check
```

Change cache/config roots if `/workspace` is not writable in the new environment.
Preserve `uv.lock`; do not refresh dependencies just to resume development.

Checkpoint baseline `d3c5eaf`: **1,155 passed, 3 skipped** in 149.49 seconds.
Prior Worker extraction `d0773b1`: **1,167 passed, 3 skipped** in 156.21 seconds.
Current visual-check follow-up: **1,202 passed, 3 skipped** in 161.57 seconds
(`uv run --frozen pytest -q`, Linux offscreen Qt).
All 35 new visual-check tests passed; focused controller/hands-free/drawing/launch/
standalone/mission-dispatch regressions passed 286 tests.
`ruff check src tests`, compilation, startup smoke, new-file formatting and
`git diff --check` passed. Startup emitted only the expected offscreen tray/raise
warnings. Independent source review found no critical or important issues.
Its constructor consistency finding was repaired with a RED-to-GREEN regression:
mutable binding dictionaries are refused at construction, preserving the typed
immutable request boundary.
The dedicated standalone journal tests cover committed intent at fake input
handoff, real review provenance, privacy, blocked operations, duplicate IDs,
stale/changed bindings, failed writes, finalization, cancellation/shutdown,
restart uncertainty and reconciliation.

One post-restart full run stalled and was terminated; a diagnostic rerun using
`pytest -vv -o faulthandler_timeout=30` completed successfully with the counts
above. The isolated grounding/hands-free suite also passed 23 tests. If the issue
recurs, preserve the current test name and traceback rather than disabling tests.
Cloud runtime suspension was a separate infrastructure issue; the repository and
uncommitted changes survived and were subsequently committed/pushed.

These checks use Linux offscreen Qt, synthetic images and fake input. **Native
Windows qualification remains pending.** They do not establish physical Windows
input correctness, real Ollama/model accuracy or reliable visual recognition.
Before rolling back record versions, preserve a consistent full-state backup;
older application versions cannot read version 4 effects. Never strip bindings or
reset unresolved effects to force compatibility.

## Starter message for another workspace

> Continue Monster from branch `work`. Read `docs/WORKSPACE_CHECKPOINT.md` and the
> linked V4 contract/authorization documents. Implementation baseline is
> `e4dafcf`; Worker extraction and bound target/completion checks are implemented.
> Inspect the current branch and changes first. Next recommended slice is extending
> typed Worker correlation to proposal/action-verification/scene-comparison results,
> preserving shared durable admission, current automatic/review policy and all
> input/cleanup checks. Do not merge `main` or expand native/guest permissions as
> part of this handoff. Verify the implementation with the relevant existing
> controller, standalone journal and mission authorization tests.
