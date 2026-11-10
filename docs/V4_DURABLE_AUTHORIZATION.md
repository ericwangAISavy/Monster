# V4 shared durable action authorization

## Delivered boundary

Journaled mission dispatch now requires the immutable operational proposal and a
fresh mapped action from slice 1. The existing `EffectCoordinator`, approval
service, SQLite ledger, Qt bridge and input worker remain the execution path.
There is no second ledger, scheduler, grant authority or cursor owner.

Slice 2a established the mission-side prerequisite. The standalone migration below
now admits its different routine-action vocabulary explicitly; the narrow mission
policy is not imposed on standalone tasks or widened to match them.

## Preparation and exact review

The controller sends the typed proposal with effect preparation. The journal
validates its schema, image action, captured-frame reference, foreground/display
scene, controller generation and any supplied mission/subtask references. The Qt
bridge supplies controller generation separately from mission generation.

Version 3 action effects add an action ID, task reference, controller generation
and process-keyed HMAC of the operational proposal. The digest covers the immutable
proposal metadata, exact payload, target description, expected result and
foreground guard. Only references and keyed digests are persisted; raw payloads,
criteria, model evidence and screenshots are not added to the ledger. Transport
hints cannot downgrade sensitivity or uncertainty in the bound proposal.

Existing exact approvals remain version 2. Their fingerprint now includes the
operational binding for version 3 effects. Human review, expiry, context checks,
active lease, cleanup and deterministic action classification remain required.
No mission action or risk category is newly allowlisted. Legacy neutral hints
`benign` and `low` project to canonical `routine`; the provider parser still accepts
only its existing risk vocabulary. Sensitive/uncertain hints are never normalized
into a neutral hint.

An operational action ID cannot mint another effect while its record is retained.
The duplicate check is inside the existing immediate SQLite transaction so two
writers cannot both prepare it. Unresolved effects continue blocking new work.

## Intent and handoff

After the existing scene recheck and target checks, the controller constructs the
mapped contract *before* requesting durable dispatch authorization. The journal
requires:

- the exact proposal, task/action identity and approved keyed binding;
- the same controller generation, distinct from the mission generation;
- the exact mapped physical payload and original capture geometry;
- a checked observation matching the bridge context and no older than two seconds;
- the correct effect ID and the existing unconsumed, unexpired exact approval.

Grant consumption and dispatch intent commit atomically before the controller can
call the input boundary. The controller then rechecks the contract against its
current proposal, capture and physical action before handoff. A mission cannot
fall back to standalone dispatch if its authorization-gate flag changes.

A refused contract before intent yields no input and no consumed grant. A changed
handoff after committed intent stops and conservatively leaves the effect
uncertain. The existing cleanup barrier and reconciliation service remain in
charge; there is no automatic replay or optimistic completion.

## Storage compatibility and rollback

The SQL ledger stays at schema 6: these are versioned JSON records in existing
action-effect tables, not a new database/table or input protocol. Version 1/2
records remain readable. New optional fields are omitted from legacy records.
Simulation-only unbound effects retain the existing version 2 behavior and
fingerprint. Live dispatch refuses legacy grants and requires fresh operational
review. Restart recovery still revokes unstarted grants and preserves possibly
dispatched effects as uncertain.

Older source versions cannot read version 3 action-effect records. Before rolling
back, stop execution and preserve a consistent backup of the entire mission state
(ledger, checkpoint database and relevant WAL state). Restore an appropriate
pre-upgrade backup rather than stripping bindings, downgrading record versions or
resetting unresolved effects. Unknown future record versions fail closed.

## Validation and remaining work

Tests cover version 3 persistence/privacy, separate generation domains, missing or
changed contracts, changed criteria/model/identity, stale/future observations,
changed physical payloads, single-use grants, duplicate preparation across two
SQLite writers, failed-intent rollback, restart uncertainty and legacy refusal.

An integration test drives the actual controller, queued Qt bridge, async runner
and SQLite service with synthetic frames and fake input. At the fake input handoff,
a separate SQLite connection confirms that the bound intent is already committed.
Missing contracts, changed post-intent contracts and changed gate mode all produce
zero input. These tests do not qualify physical Windows behavior.

The standalone admission described below and the first
[bounded Worker extraction](V4_BOUNDED_WORKER.md) are implemented. Further Worker
request extraction, native Windows qualification, grounding/verification benchmarks, cloud-egress grants and
ADR006 guest-native integration remain separate gates. Existing unknown DPI,
worker-generation and session metadata are not made known by this change.

## Standalone migration prerequisite: explicit review admission

The controller now routes action proposals through `agent.admission.admit_action`
instead of selecting automatic/review/stop inline. This is the production review
path for both modes, using host configuration and the existing deterministic
`approval_reason` rules. The immutable result records `controller-review-v1`;
it is not a grant or proof of action safety. The controller still validates input
and intent first, and still performs scene/target checks before handoff.

The policy preserves routine Enter, Start, clicks, text and navigation in automatic
mode, per-step review in supervised mode, and stop-on-review in hands-free mode.
Mission gating disables hands-free overrides. Explicit close-window requests,
sensitive summaries, uncertain model assessments, drawing intent and shortcut
review retain their previous behavior. Non-boolean host settings and non-action
proposals are rejected. Provider output cannot select a mode.

The extraction was a prerequisite; the following migration connects it to durable
standalone execution. It requires a runtime lifecycle that does not open a second
ledger owner and an automatic-authorization source that cannot masquerade as
human approval. The mission demo's narrow scratch policy
remains unchanged; no automatic grants are synthesized by this extraction.

Policy tests exercise every combination of per-step review, hands-free and mission
gating, with routine launch actions and sensitive/uncertain escalations. Existing
controller, hands-free and durable mission integration tests verify the actual
execution path separately.


## Slice 2b: standalone durable dispatch

The application attaches `StandaloneJournal` to its supervised controller using
that application's existing `missions/ledger.sqlite3`. A serialized background
worker opens the existing `Repository` and `EffectCoordinator`; SQLite never runs
on the Qt thread. The repository's exclusive `runtime.lock` remains the authority
for ledger ownership. An inactive mission runtime is closed before manual work;
if retirement is pending the application asks the user to retry shortly. A busy
ledger refuses input. There is no second database, scheduler or input executor.

A host-created, immutable standalone session pins task identity, controller
generation, instruction and review-mode settings. Provider decisions cannot
select a session or change its mode. User clarification updates the in-memory
instruction and clears old review proofs. Exact human action review records the
bound proposal digest and request reference; automatic admission creates no
human approval record. Existing validation and `controller-review-v1` execute
again on the journal worker against the original decision, including legacy risk
aliases, so canonical risk normalization cannot weaken review. Draft/canvas,
scene, operation and physical input checks remain in their existing paths.

After grounding, the controller requests intent for a fresh mapped contract. The
shared coordinator verifies the session, exact proposal/decision, native mapping,
fresh observation and review policy. Within one immediate transaction it rejects
unresolved effects or reused action IDs and inserts the bound intent. Only the
matching asynchronous reply can reach input, after another check of the desktop
lease, task generation, scene age and reviewed proposal. Cancellation, changed
contracts, failed writes and unavailable storage produce no new input. A commit
followed by cancellation is conservatively uncertain; it is never replayed.

Version 4 effects add `authorization_source` (`standalone_automatic` or
`standalone_human`) and an `authorization_ref` (task submission or exact review
request). The fingerprint also binds the instruction, original risk and mode
settings through a process-keyed digest. Raw instructions, actions, screenshots,
target descriptions and criteria remain outside the ledger. `category=unknown`
avoids claiming semantic safety from the existing routine-action review policy.
Mission version 3 effects and exact approval rules are unchanged. SQL schema 6
and the existing action-effect/audit tables remain in use; standalone does not
synthesize mission rows or mission approvals. Older versions of the application
cannot read version 4 records; the full-state backup/rollback requirements above
also apply here. Unknown record versions and missing authorization sources fail
closed.

Worker receipts and final visual observations use the existing journal methods.
The controller waits for queued journal finalization and input cleanup before
reporting completion or releasing its workspace lease. A final write failure
reports an incomplete task and retains unresolved intent. Restart recovery also
leaves possibly dispatched effects uncertain. The existing **Review uncertain
effects** control opens the shared reconciliation flow; human attestation does
not restart or repeat input. Default standalone diagnostic export behavior is
unchanged; the durable journal itself is content-free.

Tests use the real controller and shared SQLite service with synthetic frames and
fake input. They check committed intent from a separate connection at handoff,
automatic versus actual review provenance, blocked operations, stale or changed
contracts, duplicate IDs, lock/write failures, late observation failure,
cancellation/shutdown with queued intent, restart uncertainty and reconciliation.
They do not qualify native Windows input or improve model visual recognition.
The first bounded Worker extraction is implemented in [slice 2c](V4_BOUNDED_WORKER.md).
It moves one-shot read-only operations while preserving this authorization path.
Bound target/completion checks are now extracted in slice 2d of the same document.
Slice 2e adds consistent typed correlation for all operational Worker returns.
It also corrects standalone finalization to query actual unresolved effects,
rather than treating all retained history as uncertainty. Grounding/verification
benchmarks and Windows qualification remain next architecture work.
