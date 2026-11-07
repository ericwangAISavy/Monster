# Phase 6 operations and recovery

This guide describes the implemented **portable simulation** and identifies the
native gates. ADR006 review and Windows qualification are still required for
physical VM control. No live network listener or input-enable option exists.

## Placement and preflight audit

Current checkout: branch `work`, HEAD
`338ee94d5aed9262a5b9a0123a66e88783065a8a`, with the preceding uncommitted Phase 5
implementation and shortcut fix preserved. No AGENTS.md was found. Existing
Python/uv/lockfile/CI conventions were reused, without new dependencies.

| Prerequisite | Evidence | Status |
| --- | --- | --- |
| Phase 1 | Mission contracts, Repository, LangGraph SQLite saver | Present |
| Phase 2 | WorkspaceGate, MissionGuiBridge, AsyncGuiSubtaskRunner, independent worker stop | Present |
| Phase 3 | EffectCoordinator, policy, exact review/intent journal, reconciliation | Present |
| Phase 4 | MissionScheduler, global budgets, pause/resume, six-stage fake runner | Present |
| Phase 5 | strategic contracts/catalog/planner/service, admission and evidence/revision tests | Present |
| Baseline | Full pytest before Phase 6 | 766 passed, 3 skipped, 101.48s |
| Windows Phases 2–5 | Existing qualification docs | PENDING |

## Operator control flow

Personal Desktop remains default, with its original hotkeys/controller. Open
Workspaces explicitly, register a single guest, select simulation, verify the fake
pairing ceremony and start the reviewed editor fixture. No host mouse movements,
coordinates, arbitrary strings or input commands are sent to the guest. The
companion continues following the host's own cursor; guest statuses change only
its expression. Remote review uses the existing pill and a guest nonce.

The simulation's guest role owns one existing MissionCoordinator and ledger. It
compiles the existing reviewed editor plan with the opaque guest workspace ID.
Guest-local associations bind that mission and its effects to the original guest
instance/session. Each new control challenge binds the current session and exact
scheduler consent. Nothing transfers a partially executed personal mission into
another workspace. The host contains no second writable authoritative ledger.

During the portable UI simulation the guest role runs on the async worker thread
and uses a fixture directory on the same machine. This is not a separate physical
Windows session. A future reviewed native guest must run the existing Qt bridge,
capture and input worker inside the actual guest; those paths are not connected
by this change.

## Stop and pause

- Host Stop sends the highest-priority fixed stop request and immediately
  invalidates queued UI approvals. It first says acknowledgement pending.
- A delivered acknowledgement reports simulated disarm only after coordinator
  cancellation completes. An unresolved effect remains explicitly unresolved.
- Dropped command or acknowledgement yields **Stop unconfirmed** on the host.
  Never infer physical cleanup from a transport ACK.
- The guest watchdog checks authenticated link freshness (default five seconds,
  configurable two to ten), grant expiry and clock continuity. Its scheduler guard
  checks link/session/consent before each stage even if the timer is delayed.
- A partition disarms and pauses, revokes challenges and advances generation.
  Heartbeats and reconnecting do not authorize resumed work.
- Unknown cleanup/worker crash quarantines the workspace. New missions are refused.
- In the future native pilot the existing guest-local Ctrl+Alt+X must remain
  independent of host, model and network; the simulator does not claim it tested
  a Win32 hotkey or physical worker cleanup.

Pause-after-stage uses the existing scheduler's boundary mechanism. Stop/manual
takeover uses immediate coordinator cancellation. A mode switch with active or
unknown cleanup refuses the switch and requests stop; it does not migrate input.

## Reconnect, restart and reconciliation

A reconnect refreshes the signed status/session binding. The user must explicitly
request resume and approve a fresh challenge. New guest process identity and
session generation invalidate old frames. The guest reopens the existing ledger,
recovers unresolved action effects, revokes stale challenges and advertises prior
resumable mission references without executing them. Verified outcomes are retained.

A possibly dispatched effect or a receipt without visual verification stays
uncertain. Observe-only reconciliation exposes that state but does not let a
network payload rewrite the journal or replay input. Inspect the guest console and
use the existing guest-local reviewed reconciliation mechanism before requesting
fresh continuation. A fresh task while uncertainty exists is refused.

Session lock/logoff, secure desktop, RDP/basic/enhanced switch, display/scale change,
provider/egress change, VM suspend, clock jump and manual takeover invalidate current
scope. Synthetic events exercise these transitions; actual native sensors and
supported session combinations remain unqualified. Black/protected capture is not
usable for input. A synthetic preview is explicitly separate from inference data.

A snapshot restore/clone requires new identity, re-pairing and inspection. Do not
assume a VM snapshot is a safe mission checkpoint. The portable restore event
rotates instance and pairing identity and blocks the session; actual live-memory
rollback detection cannot be claimed from that event simulation.

## Persistence, backup and retention

Ledger schema 6 additively creates `guest_workspace_sessions`,
`guest_mission_bindings`, `guest_control_requests` and `guest_control_challenges` in
the existing guest-role ledger. Earlier mission, effect, approval, strategic,
reservation and LangGraph checkpoint records remain unchanged. A process boot
increments guest generation; no authorization is restored from a disk status.

Request reservations commit before handlers. Duplicate mutations are rejected,
including after a lost response. SQLite failure means no new dispatch. Opaque
request fingerprints are retained to prevent replay, with hard request caps;
challenge rows for pruned terminal missions and their workspace associations are
removed by the existing terminal retention path. Pairing keys and previews are RAM
only. Diagnostic history and host projection are bounded; no raw frame logging.

Stop Monster and preserve a consistent backup of the *whole* guest state directory
(checkpoints, ledger and WAL files), or use the SQLite backup API. Do not synchronize
it to a second active host journal. Unknown future schemas fail closed. To roll
back code, restore the whole pre-upgrade backup; do not decrement user_version or
reset effects. Forgetting a host registration must not erase the guest ledger.
Rebuild a disposable guest with a fresh workspace ID and explicit new pairing.

## Pairing rotation and forgetting

Revoke pairing to disarm, invalidate pending challenges and prevent further
messages under that key. Reconnect with the same still-valid RAM identity only
rehydrates status; it cannot resume. A new guest process/clone/restore requires
fresh explicit pairing. Stop and select Personal Desktop before forgetting the
host registration. No key/cookie/provider credential is copied between machines.
For real deployments these operations require the pending TLS/OS-store review.

## Phase 7 gate

Do not proceed to real-world account, social, application-submission, financial
or deployment workflows from these fixtures. First review/integrate the native
transport, finish Windows Phases 2–6 qualification, measure multiple controlled
runs and recovery failures, and review a narrow authorized workflow with exact
external-effect policy. No unrestricted account automation or public posting is
introduced here.
