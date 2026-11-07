# Phase 6 Windows / Hyper-V qualification — PENDING

**Overall native status: PENDING.** Native Phase 2/3/4/5 qualification is also
PENDING. No Windows guest, Hyper-V session, real network pairing, TLS transport,
Win32 emergency stop, guest screenshot, physical input or LAN model was exercised.
No native PASS certificate exists or can be minted by the simulator.

## Delivered versus gated

| Area | Current state |
| --- | --- |
| Typed workspace/session/control protocol | Implemented and tested portably |
| Explicit simulation pairing and replay/expiry/authentication guards | Implemented; RAM HMAC over in-memory frames, **not TLS** |
| Guest-owned existing engine/ledger | Implemented with existing fake runner |
| Host secondary Workspaces view, nonce review, status, preview, stop | Implemented and tested offscreen |
| Partition/session/clock/restore fault handling | Implemented for deterministic synthetic events |
| Guest module entry and read-only diagnostics | Implemented, disarmed, no native enable switch |
| Guest-initiated pinned mutual TLS, Windows OS credential storage | Pending ADR006 design review and implementation/integration |
| Native Qt controller/capture/independent input-worker guest wiring | Disabled pending prerequisite/native review |
| Actual isolation, pointer independence, worker cleanup, session support | PENDING native qualification |

The supplied prompt §16 requires reviewer approval before network/input integration;
§22 permits a documented portable-only deliverable when native qualification is
blocked. This milestone satisfies that portable path. It does **not** satisfy the
physical release gate or claim that a real Windows VM is controllable.

## Automated evidence map

| Matrix | Portable checks |
| --- | --- |
| P6-01–04 | Prerequisite audit, personal default, no native input hooks, unsupported/native gate refuses PASS overrides |
| P6-05–09 | Offline/expired/revoked/unpaired/identity mismatch refusal; pairing requires explicit console verification |
| P6-10–15 | Bounded JSON, unknown operations/fields, durable replay guard, exact nonce/observation binding, cross-guest/session rejection |
| P6-16–17 | Native-input constructor patched to fail; synthetic guest geometry independent from a changed host cursor fixture |
| P6-18–23 | Host disappearance, watchdog, hung guest execution, dropped command/ACK, local disarm, honest stop projection |
| P6-24–29 | Synthetic lock/secure desktop/takeover/display/provider/black-capture events revoke scope; policy remains existing Phase 3 authority |
| P6-30–38 | SQLite refusal/redaction, pre-dispatch/intent/receipt faults, boot/reconnect/clone/restore identity rejection; prior policy/handoff regressions |
| P6-39–42 | Explicit preview consent, negative-origin transforms and ephemeral buffer, hostile command text refusal, provider/scene invalidation |
| P6-43–50 | One controller/guest, one active mission, quarantine, clock/suspend handling, no raw secret/frame persistence, additive schema 5 migration and full regressions |
| P6-51–60 | Qt responsive during an indefinitely hung fake objective, monotonic replies, lost-review revocation, takeover, no reconnect replay, six-stage pilot, receipt-only uncertainty, redacted errors, native gate, uncertain-task refusal |

No table row above is a claim of actual TLS authentication or Windows API testing.
Fault controls include DROP, DELAY, DUPLICATE, REORDER, CORRUPT, REPLAY,
DISCONNECT/RECONNECT, ROTATE_IDENTITY, GUEST_REBOOT, HOST_RESTART, SESSION_LOCK,
DISPLAY_CHANGE, SUSPEND_VM, RESTORE_SNAPSHOT, CRASH_BEFORE_DISPATCH,
CRASH_AFTER_INTENT, CRASH_AFTER_RECEIPT and CLEANUP_UNKNOWN. Real process/ledger
restart is separately exercised by reopening the guest service with the same DB.

## Required native checklist, after design/integration review

1. Record host/guest Windows builds, editions, license arrangement, Hyper-V mode,
   hardware/resources, source and package digests, Python/Qt, DPI/displays and model
   identity/runtime/destination. Finish Phase 2–5 native prerequisites.
2. Review the single-authority guest placement and complete mutual-TLS/OS-store
   implementation security review; verify both identities on visible consoles.
3. Use one disposable standard-user guest, restricted private networking, minimal
   host sharing, patched software and an unsaved disposable Notepad document.
4. Verify unpaired/expired/rotated identities reveal no private status or input.
5. Verify observe-only capture and preview use the actual guest session and exclude
   Monster overlays. Refuse blank/protected/locked/secure desktop frames.
6. Verify every native action uses the original guest controller, exact Phase 3
   review/intent journal, fresh scene, display transform and one input worker.
7. Run the harmless six-stage editor sequence; require guest visual evidence and
   worker cleanup. Change host pointer/focus continuously and verify independence.
8. Test guest-local Ctrl+Alt+X during model latency, approval and bounded input,
   independently of host network. Record physical held-key/button cleanup.
9. Test host stop, lost stop and lost ACK. Record request latency and cleanup
   evidence; never label an unconfirmed remote stop safely completed.
10. Test lock/logoff, UAC/secure desktop, VMConnect basic/enhanced, RDP connect/
    disconnect, resolution/DPI/monitor changes and manual guest takeover. Qualify
    each supported combination explicitly or document refusal.
11. Test host/guest process crash, model unavailability, network partition, pause/
    save/restore, wall/monotonic jumps, VM clone and snapshot rollback. Require
    new identity/consent/reconciliation; no duplicate typing or external effect.
12. Inspect guest SQLite/WAL, host preferences, logs and backups for secrets,
    screenshots, typed content, raw frames or model reasoning.
13. Measure host responsiveness, resource footprint, guest capture/inference,
    status/approval latency, reconnect time, independent stop latency, failures,
    duplicate attempts, false completion and interventions. Separate evidence from
    simulation, real observations and human attestation.
14. Record PASS/FAIL/PENDING per case with operator-reviewed evidence refs,
    supported session limits, exact configuration binding, test time and expiry.

No production accounts, CAPTCHA/MFA bypass, public sends, purchases or deployments
belong in this checklist. Do not generate a PASS file from automated fixtures.
