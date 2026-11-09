# V4 slice 2a: durable operational binding for mission input

## Delivered boundary

Journaled mission dispatch now requires the immutable operational proposal and a
fresh mapped action from slice 1. The existing `EffectCoordinator`, approval
service, SQLite ledger, Qt bridge and input worker remain the execution path.
There is no second ledger, scheduler, grant authority or cursor owner.

This is the mission-side prerequisite of the shared-authority migration, not a
claim that standalone execution has been migrated. Standalone tasks retain their
existing policy and projection behavior. Their different routine-action vocabulary
must be admitted explicitly in the next migration; the narrow mission policy is
not silently imposed on them or widened to match them.

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

Remaining slice 2 work includes standalone durable task admission and explicit
mode-specific policy preservation, followed by bounded Worker extraction. Native
Windows qualification, grounding/verification benchmarks, cloud-egress grants and
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

This change is a bounded policy-extraction prerequisite, **not standalone durable
execution**. Standalone actions still have no durable intent. Connecting their
admission result to the shared journal requires a runtime lifecycle that does not
open a second ledger owner, plus an explicit automatic-authorization source that
cannot masquerade as human approval. The mission demo's narrow scratch policy
remains unchanged; no automatic grants are synthesized by this extraction.

Policy tests exercise every combination of per-step review, hands-free and mission
gating, with routine launch actions and sensitive/uncertain escalations. Existing
controller, hands-free and durable mission integration tests verify the actual
execution path separately.
