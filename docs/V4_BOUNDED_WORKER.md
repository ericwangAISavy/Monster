# V4 slice 2c: bounded read-only Worker

This incremental extraction separates operational work from the Qt lifecycle.
It does not replace the controller, LangGraph, the shared effect coordinator or
the input worker. Native Windows and real-model qualification remain pending.

## Actual boundary

`agent/worker.py` contains a stateless `BoundedWorker`. Each invocation performs
exactly one of these operations:

- `propose`: one existing provider decision request using a captured model,
  instruction, image and bounded history snapshot.
- `verify`: one existing visual action-assessment request using the captured
  action, expected outcome and optional before image.
- `compare`: the existing scene comparison, on the existing ProviderJobs thread.
- `prepare`: the existing intent/action validation and physical coordinate
  mapping, with host-supplied one-click/close state. This is not review admission.
- `ground` / `assess`: the existing operational contract projections, including
  both drag endpoints, generation, foreground/display and criterion provenance.

The Worker has no input, ledger, grant, capture or scheduling port, no owned thread
and no retry/recursive loop. It returns existing decisions, assessments or
contracts whose execution authority is `none`. Model-reported visual success is
still an assessment, not an independent proof or an authorization.

The Qt controller continues to own the task deadline, counters, phase changes,
latest capture, rejected/duplicate action history, draft/canvas target checks,
manual focus confirmation, review policy, queued job identity, cancellation and
terminal outcomes. Slice 2d below moves readiness and whole-task visual inference
behind the Worker; their interpretation remains with the controller. Provider
probing remains a compatibility operation. LangGraph remains the outer orchestrator.

## Requests and configuration binding

`VisualContext`, `ProposalRequest` and `VerificationRequest` are frozen RAM-only
packets. History uses an immutable tuple; executable action payloads reuse the
canonical immutable `ActionPayload` and yield a fresh dictionary for a provider.
Capture bytes reuse the existing immutable `Observation`; no capture session,
worker generation or DPI metadata is invented. Packet representations omit raw
instructions, images, history, expected results and payloads. These packets are
not a new wire/storage schema and are never added to default diagnostics or SQL;
existing metadata-only contract exports remain unchanged.

The controller snapshots its frozen provider configuration before queuing a
proposal or verification job. The provider factory and Worker both receive that
snapshot. A result for the current job is refused if the host configuration has
changed, even if it otherwise looks valid. Existing job identity, active/paused
state and cancellation-generation checks still reject late replies. A refused
verification-packet construction terminates incomplete through normal cleanup;
it cannot escape the Qt callback or turn a possibly dispatched action into
success.

This also repairs a reproduced pre-existing queue race: the old deferred lambda
could read a newly selected model/configuration after the request was queued.
The new request uses its captured selection and fails closed on changed host
settings. No silent provider/model switch is introduced.

## Input authority is unchanged

Host `admit_action` still selects automatic/review/stop disposition. Routine
Enter/click, per-step approvals and hands-free stop-on-review semantics are
unchanged. Before either mission or standalone input:

1. The host validates the proposal, duplicate history, fresh scene and target.
2. The same shared coordinator records/consumes the appropriate exact binding and
   commits dispatch intent in the existing ledger.
3. The controller revalidates the current mapped contract, generation, lease and
   capture freshness before calling the existing input bridge.
4. Input receipts, visual outcomes, journal finalization and cleanup barriers
   retain their original owners. Uncertain input is preserved for explicit
   reconciliation, never automatically replayed.

No ledger/schema version, native operation allowlist, permission, guest transport
or qualification gate changed. The guest remains simulation-only under existing
ADR006 prerequisites.

## Validation

The baseline at checkpoint `d3c5eaf` was **1,155 passed, 3 skipped**. Worker unit
and controller integration tests cover private immutable snapshots, one-call
behavior, cancellation/error propagation without retries, host configuration
changes, exact physical mapping including both drag endpoints, generation and
foreground refusal, one-click limits, model-assessment provenance, host review
before any intent, and invalid verification packets leaving the shared effect
uncertain after cleanup. Existing standalone/mission tests inspect committed SQL
intent from a separate connection at fake input handoff and retain failed-write,
changed-binding, cancellation, finalization and restart tests.

Final frozen suite and tool results are recorded in
[the checkpoint](WORKSPACE_CHECKPOINT.md). All input/model evidence is synthetic
or mocked/offscreen. It establishes no physical Windows or real-model accuracy.

## Next small slice

After slice 2d, extend the same typed request/result correlation to the existing
proposal, action-verification and scene-comparison operations, which still return
legacy Decision/Verification/bool values under host job/configuration guards.
Keep host interpretation, action authorization and final completion separate.
Grounding/verification benchmarks and native Windows qualification remain
necessary before broader architecture or reliability claims.

## Slice 2d: bound target and completion checks

The follow-up extraction adds `BoundedWorker.check` for four explicit, read-only
operations: draft readiness, drawing/canvas readiness, app-launch observation and
whole-task visual assessment. The last two use the existing verification provider
methods; app-launch observation supplies an application-owned `screenshot` marker
to that read-only provider method, never to the input executor. Provider probing
remains a compatibility operation in the controller.

`agent/worker_checks.py` defines an immutable RAM request plus version-1 strict
binding and result records. A unique request reference binds task, operation,
hashed model identity and the captured ObservationFrame, including controller
generation and foreground/display metadata. Target action/criterion text and PNG
bytes stay in RAM. Result evidence is excluded from default serialization and
representation. Exported results are explicitly `model_assessment` with
`execution_authority=none`; they are not journal outcomes or approval grants.

The controller captures the model/configuration and request before queueing. It
requires the current job's binding to exist, then revalidates the returned
schema, request/task/operation/model/frame binding, current generation, phase,
configuration and exact observation before interpreting readiness/completion.
Missing bindings do not fall back to legacy raw result handling. Unknown fields,
future/bool versions, grant-like fields and operation-incompatible status values
fail closed. A success from another request cannot authorize input or finish a
task. Late replies after cancellation retain the existing job/active-state gate.

Draft/canvas check failures still stop before intent; successful readiness still
requires the existing fresh scene recheck. Whole-task success remains a host
interpretation subject to existing journal finalization and cleanup barriers.
No screenshot, input operation, provider routing, native permission, journal
schema or guest qualification was added or relaxed.

The 35 new tests cover all four calls, immutable action snapshots, status/schema
rejection, private evidence omission, exact host bindings (including private
foreground guards), missing-binding refusal, no-intent/no-input failures,
forged completion, cancellation and a late completion claim. Existing hands-free,
launch, drawing, draft and durable authorization regressions remain applicable.
Final full-suite evidence is recorded in the checkpoint.
