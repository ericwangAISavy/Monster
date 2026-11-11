# Monster workspace checkpoint

This handoff is saved in Git so another workspace can resume without the original
chat, uploaded files, local caches, or cloud environment instance.

## Resume point

- Repository: `https://github.com/ericwangAISavy/Monster.git`
- Development branch: `work` (do not assume these changes are on `main`).
- Prior implementation baseline: `e4dafcf` — Journal standalone input before native dispatch.
- Prior checkpoint: `d3c5eaf`; first Worker extraction: `d0773b1`.
- Prior visual-check implementation/checkpoint: `fb60812` / `d89734f`.
- Prior correlated Worker/finalization implementation: `c191266`.
- Prior stage 3 benchmark tooling: `23b1af1`.
- Prior stage 4 evidence assessor: `0980901` (confirmed on remote `work`).
- Prior correlated compatibility probe: `950b6a6`.
- Current implementation: the whole-branch review/repair commit containing this
  checkpoint, on `work`; see [review findings](REVIEW_2026_11_11.md),
  [stage 4](V4_STAGE4_NATIVE_TRIALS.md),
  [stage 3](V4_STAGE3_RELIABILITY_BENCHMARKS.md) and
  [the extraction boundary](V4_BOUNDED_WORKER.md).
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
8. **Correlated operational outcomes:** slice 2e extends the same bindings to
   proposal, action-verification and both-frame scene-comparison results. Private
   proposal JSON snapshots retain exact risk/provenance without mutable payloads
   or raw exports. Host action/criterion comparisons precede interpretation.
9. **Resolved finalization:** standalone completion now queries actual unresolved
   effects after `finish_attempt`; its returned history list is not an uncertainty
   flag. Known-success records no longer cause false incomplete results. Unknown
   effects, cleanup and failed-write barriers remain unchanged.

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
| Common bindings and typed operational result envelopes | `src/monster/agent/worker_results.py` |
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

All seven visual Worker result phases and the operational compatibility check
now require correlated typed outcomes.
Stage 3 tooling and stage 4 per-attempt evidence assessment now exist; next run
labelled real-model measurements and native
Windows qualification against the actual source/model/configuration. Measure
wrong-target actions, duplicate attempts, false completion, verification
false-positive/negative cases and latency; distinguish model, tool and executor
errors. Use synthetic/private opt-in fixtures without default content persistence.
Start with the focused unsent Slack draft workflow and disposable editor/canvas
checks already described in the qualification documents. Do not enable guest or
unrestricted desktop execution to demonstrate the architecture.

The Worker remains stateless and read-only; Qt retains host interpretation,
limits, lifecycle and authorization, and LangGraph remains the outer orchestrator.
Operational provider probing now uses the same bounded Worker via a no-frame
request/result contract; the settings-dialog probe remains separate. Any further
phase-handler consolidation must preserve this authority split. Later interactive
orchestration, egress credentials and declarative skill work requires its own
scope/qualification review; do not claim all of V4 is complete from these slices.

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

Remaining evidence work includes live grounding/verification measurements and Windows qualification,
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
Prior visual-check follow-up: **1,202 passed, 3 skipped** in 161.57 seconds.
Prior correlation/finalization validation: **1,229 passed, 3 skipped** in
397.96 seconds using `uv run --frozen pytest -vv -o faulthandler_timeout=30`
(Linux offscreen Qt, serial process). All 27 new correlation/finalization cases
passed within that run. This wall time is not a native/model latency benchmark.
Prior slice 2d visual-check tests: 35 passed; its focused controller/hands-free/
drawing/launch/standalone/mission-dispatch regressions passed 286 tests.
Current slice 2e adds 27 correlation/finalization cases. Standalone/new-correlation
regressions passed 57 tests before the final coverage additions; mission, drawing
and grounding regressions passed 37 tests after compatibility/fixture fixes.
`ruff check src tests`, compilation, startup smoke, new-file formatting and
`git diff --check` passed. Startup emitted only the expected offscreen tray/raise
warnings. Independent source review of slice 2e found no critical or important
issues and confirmed the finalization predicate. Its pending-action coverage
suggestion was added for both verification and scene comparison. Slice 2d
constructor consistency coverage remains intact: mutable binding dictionaries
are refused at construction.
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
> `e4dafcf`; all operational Worker replies are now correlated and standalone
> finalization distinguishes resolved history from uncertainty. Inspect the branch
> and changes first. Stage 3 read-only benchmark tooling and synthetic replay are
> implemented, alongside the stage 4 per-attempt native evidence assessor.
> Next recommended work is live selected-model measurements on
> labelled fixtures/held-out data, followed by native Windows qualification,
> preserving shared durable admission, current automatic/review policy and all
> input/cleanup checks. Do not merge `main` or expand native/guest permissions as
> part of this handoff. Verify the implementation with the relevant existing
> controller, standalone journal and mission authorization tests.

## Stage 3 read-only measurement slice

`src/monster/benchmarks/reliability.py` reuses the existing BoundedWorker and
OllamaProvider without capture, input, ledger or permission changes.
`reliability_cases.py` validates bounded hashed PNG manifests; synthetic cases,
recordings and their reproducible generator live in `tests/fixtures/reliability`.
Run commands and metric semantics are in the linked stage 3 document.

The 54-trial replay completed and detected intentional recording faults. It is
not a model benchmark or Windows qualification. The read-only private Ollama
access probe returned HTTP 403, so availability was not established; no live
inference or configuration change was performed. Native qualification remains
pending. Metadata reports keep replay/injected/live modes separate and leave
unmeasured native/executor metrics null.

One initial full-suite run stalled in the existing mission graph compiler before
reaching the new benchmark tests and was terminated; its faulthandler traceback
was preserved at `/tmp/stage3-full.txt` in this workspace. A preliminary overlapping
focused controller run also had three cleanup wait timeouts. These runs are not
counted as passing evidence. No production limits or checks were loosened.

Stage 3 verification: `uv sync --frozen`, `ruff check src tests`, formatting of
all four new Python files, compilation, startup smoke and staged diff checks
passed. Linux read-only native preflight reported unavailable, with no capture
or input. The first completed full run reported **1,250 passed, 3 skipped,
3 failed** in 414.37 seconds. Failures were existing launch-review cleanup,
UI-Venus deadline timing and guest-stop timing tests. All three complete affected
modules then passed together: **31 passed in 18.24 seconds**, with no code or
limit changes between runs. All **24 new benchmark tests** passed both in isolation
(18.08 seconds) and in the completed full run. Preserve these intermittent failures
rather than claiming every run passed. Logs remain under `/tmp/stage3-full-final.txt`
and `/tmp/stage3-timing-rerun.txt` in this workspace.

Final serial confirmation, with no production code or timeout changes after the
prior run: **1,253 passed, 3 skipped in 307.29 seconds** using
`uv run --frozen pytest -vv -o faulthandler_timeout=30` (Linux offscreen Qt).
All three previously failing tests passed. Full per-test diagnostics are at
`/tmp/stage3-full-confirmation.txt` in this workspace. Those temporary logs are
not needed to reproduce the checked-in tests and are not qualification evidence.
This is the final authoritative regression run for this slice, while the earlier
intermittent failures remain documented above.

## Stage 4 per-attempt evidence slice

`src/monster/benchmarks/native_trials.py` adds a read-only template and assessor
for the focused/unfocused × approval on/off unsent Slack draft matrix. It reuses
`qualification.assess_native` and existing explicitly exported JSON run reports;
no controller, executor, authorization, model settings or ledger changes.

The default template contains 12 unmeasured slots (three per case). Reports bind
by local confined path, SHA-256 and unique run ID. Exact protocol instruction,
full configuration, runtime/source/worker provenance, idle terminal status and
cleanup must agree. Independent operator observations establish visible outcomes;
failed and unknown attempts block review. Unknown incident counts remain null.
Changed configurations cannot be pooled. Output includes metadata-only counts,
per-trial operation wait totals and operator-reported failure attribution/stop
outcomes, with no raw task/action text, screenshots or model evidence.

The inventory is operator-reported, not attested. It never grants execution or
sets native/model qualification. Windows preflight in this workspace again
reported `unavailable`; no native input or desktop capture was performed. Real
selected-model benchmark measurements, Windows tool/DPI/focus/stop checks and the
actual repeated draft trials remain pending. **Do not proceed to broader step 5
execution based on these synthetic tests.** See the stage 4 document for exact
Windows commands, local report handling and the observations to collect.

Test-first implementation: 38 new cases initially failed because the module did
not exist, then passed. Additional edge/review tests reproduced unknown counts
being reported as zero, oversized-file CLI errors, incomplete configuration and
contradictory cleanup/running metadata. These were repaired without changing
execution or production limits. Focused qualification/diagnostics/stage 3/new
regressions: **115 passed in 17.12 seconds**, including **59 new assessor cases**.
Independent source review's two important findings (configuration completeness and
cleanup/phase contradictions) were covered by failing tests and fixed. Synthetic
Windows-shaped records are validator fixtures, not native evidence.

The executor disconnected near the end of the first stage 4 full run. After
reconnection, its retained log showed **1,310 passed, 3 skipped, 2 failed in
442.51 seconds**. The failures were:

- `tests/workspaces/test_control.py::test_session_scope_changes_invalidate_existing_approvals[RESTORE_SNAPSHOT]`
  (`LinkError: delivery_or_authentication_unconfirmed`).
- `tests/workspaces/test_workspace_ui.py::test_host_application_keeps_personal_default_and_routes_guest_review`
  (8-second Qt wait timeout).

Both complete affected modules passed unchanged after recovery: **91 passed in
20.32 seconds**. This does not establish the disconnect as their root cause;
the failures remain documented. Logs are `/tmp/native-trials-full.txt` and
`/tmp/native-trials-recovery-focused.txt` in this workspace. No production code,
limits or test timeouts changed in response. Frozen setup, lint, compilation,
formatting, staged diff checks and startup smoke passed after recovery; smoke
emitted only expected offscreen tray/raise warnings. The staged source and docs
survived the disconnect intact.

Final post-recovery serial confirmation: **1,312 passed, 3 skipped in 265.45
seconds**, using the checkpoint's frozen Linux offscreen command with per-test
progress and `faulthandler_timeout=30`. Both prior failures passed. The log is
`/tmp/native-trials-final-confirmation.txt` in this workspace. This is the final
regression evidence for this slice; the earlier failures remain documented above.
A direct CLI template → assess smoke returned the expected blocked inventory:
12 planned, 0 attempted, 0 successful, review ineligible, execution unauthorized,
native qualification false, exit 1. No native input or capture occurred.

Completed changes are committed/pushed to `work` in the commit containing this
checkpoint; `main` is untouched. Next work requires actual selected-model and
Windows evidence using the linked stage 3/4 protocols, not another executor or
permission expansion. Preserve uncertain effects and current qualification gates.

## Correlated operational compatibility check

The next portable implementation slice closes the remaining operational probe
boundary. New `agent/worker_probe.py` binds a frozen config and selected model to
a nonce/task/controller generation without a desktop frame. The existing Worker
performs the provider check once; the controller accepts only the exact positive,
current typed result before capture. It no longer treats arbitrary error-free
objects as compatibility success, and queued provider creation uses submitted
settings rather than current mutable host settings. Input authorization, ledger,
worker ownership, review/hands-free behavior, native/guest gates, model settings,
provider probe schema/timeouts and UI are unchanged. This does not fix or diagnose
private-server timeouts or prove model/Windows control reliability.

Fail-first tests reproduced missing result validation; a separate queue regression
failed with the old provider closure and passed with the frozen snapshot. Focused
Worker/correlation/visual/supervised regressions passed **251 tests in 41.96
seconds**, including **24 new probe tests**. A preliminary test pass had 11 fixture
cleanup errors because the new tests called nonexistent `shutdown()` instead of
`close()`; corrected in tests, with no production cleanup/timeout changes.
Independent source review found no critical or important findings. Its sandboxed
async test attempt encountered known wakeup timeouts; authoritative controller
validation uses the escalated serial process. Minor deferred coverage: direct
forged CompatibilityResult `model_construct`/invalid `model_copy` fields; the
shared Record revalidates instances, and nonce/raw-result refusal is covered.

Native Windows qualification and live-model grounding/verification measurements
remain pending. This follow-up does not authorize broader step 5 execution.

Final frozen serial suite for the compatibility follow-up: **1,336 passed,
3 skipped in 169.11 seconds**, using `uv run --frozen pytest -vv
-o faulthandler_timeout=30` on Linux offscreen Qt. Log:
`/tmp/worker-probe-full.txt` in this workspace. Frozen setup, lint, compilation,
new-file formatting, startup smoke and diff checks passed. Startup emitted only
expected offscreen tray/raise warnings. Commit/push target remains `work`, with
no merge into `main`. Actual Windows and live-model qualification remain pending.

## Whole-branch review and repairs

See `docs/REVIEW_2026_11_11.md` for inspected scope and concrete reproductions.
Repairs cover four defects: settings compatibility policy comparison, invalid UI
probe acceptance/cache, pointer takeover between double-click events, and stale
host guest-stop projection after resumed work/session changes. Existing shared
ledger/intent, cleanup, review/hands-free policies and native/guest gates remain
intact. No settings/model fallback, timeout/limit increase or new execution path.

Affected-module confirmation: **175 passed in 38.37s**. Probe/forged-envelope
coverage: **27 passed in 0.52s**; the prior deferred forged flag/metadata/authority
variants are now covered and refused before desktop capture. Independent final
repair review found no important issues. Earlier focused transport/Qt/input
failures and the corrected start-versus-resume fixture are documented in the
review report. No failed run is represented as passing. Native Windows and
actual-model grounding/verification evidence remain pending.

Final review-repair frozen serial suite: **1,353 passed, 3 skipped in 360.38
seconds** (`uv run --frozen pytest -vv -o faulthandler_timeout=30`, Linux offscreen
Qt). Log: `/tmp/whole-review-full.txt`. Frozen setup, lint, compilation, startup
smoke and diff checks passed. Source review and synthetic regressions do not
qualify physical Windows or actual model reliability. Exact earlier focused
failure names are retained in the review report. Changes are committed/pushed
to `work` in the commit containing this checkpoint; no merge into `main`.
