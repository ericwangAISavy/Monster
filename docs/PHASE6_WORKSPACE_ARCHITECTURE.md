# Phase 6 workspace architecture / ADR 006

Status: portable implementation selected; live network/input integration requires
review. Native Windows qualification is PENDING.

## Decision

Keep the complete existing mission engine, scheduler, LangGraph checkpoints,
SQLite effect journal, capture, model adapter, Qt-safe bridge and independent
input worker inside a dedicated Windows guest. The physical host retains only
Monster's companion, human review and workspace connection/status preferences.
No host-side input may target VMConnect/RDP, and no raw input RPC exists.

The portable implementation uses a deterministic in-memory link and the existing
fake runner, with a guest-owned ledger. It is explicitly a simulation, without
TLS sockets, real pairing credentials, real screenshots or native input. It does
not represent successful VM isolation or LAN authentication.

For a later reviewed native integration, select one topology: guest-initiated
mutually authenticated TLS to a host-only listener, provisioned with manually
verified certificate pins and OS-protected per-device credentials. No public
listener, TOFU, plaintext transport or cloud fallback is acceptable. Pairing
secrets must use the Windows credential store. Endpoint configuration is private
and separate from mission/checkpoint contracts. This topology is an ADR, not a
claim that a live transport has been implemented or qualified.

The supplied Phase 6 prompt §16 Slice A states: “Reviewer approves the design
before network or input integration.” Accordingly this change implements and
validates the portable path; no real network listener or Windows execution is
connected. The prompt's §22 deliverable 4 permits a documented portable-only
result when native qualification is blocked. Phases 2–5 native qualification
remains PENDING, so supervised VM execution cannot be enabled here.

## Safety boundaries

- Exactly one registered guest per host; personal mode stays the default.
- Strict versioned bounded control messages, no generic method dispatch or code.
- Explicit simulation pairing, RAM-only credentials, scoped single controller.
- Workspace/guest-instance/generation/sequence/expiry binding on every frame.
- Guest-owned durable request reservations reject duplicate mutations across
  retries. No network ACK is evidence of task completion.
- Guest boot, session/display/provider changes, disconnect, time jumps and
  restore invalidate grants. No reconnect or boot auto-resume.
- Guest stop cancels through the existing coordinator. Unknown cleanup or
  unresolved effects prevent new work. Host shows stop unconfirmed on lost ACK.
- Preview is a single consent-scoped synthetic frame, not a stream, kept in RAM.
- Real Windows capture and input remain behind the existing reviewed bridge and
  native qualification; this implementation has no native-enable switch.

The full implementation/validation report and remaining operator steps are in
`PHASE6_OPERATIONS_AND_RECOVERY.md` and `WINDOWS_PHASE6_QUALIFICATION.md`.
