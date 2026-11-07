# Phase 6 security model and review

Scope: portable control-plane simulation. Live TLS/OS credential integration is
pending ADR006 review and native qualification. RAM HMAC authenticates simulator
frames; it supplies **no network encryption** and is never advertised as TLS.
There is no network listener, live control port, public endpoint or native input
path in this implementation. Do not deploy FakeTransport as a LAN transport.

## Assets and boundaries

| Asset/boundary | Authority and protection |
| --- | --- |
| Guest mission, plan, budgets and effects | Existing guest-role SQLite ledger and LangGraph, not host projection |
| Host user intent | Explicit UI review of one guest-issued nonce/digest; model text has no control callback |
| Guest application/desktop | Existing controller, scene/policy/worker boundary in future native integration; presently fake runner only |
| Host personal desktop | Personal controller is not invoked by independent-workspace commands; no VM-viewer automation |
| Pairing identity | Explicit simulated console verification, random per-grant RAM key; never checkpointed or logged |
| Screenshots/model data | No real screenshot/model egress in simulation; one synthetic preview requires explicit consent |
| VM snapshots and shared resources | No automatic restore/resume, clone identity reuse or host-sharing configuration |

## Implemented protocol protections

Strict schema 1 uses opaque random IDs, finite bounded timestamps, a maximum
ten-second command lifetime, binding to workspace + guest instance + session
reference + generation + display/provider digests, monotonically increasing
sequences and request IDs. Replies carry the originating request ID and event
sequence, and nested statuses/challenges/previews must have the same binding.

Frame reads are capped at 64 KiB, nested JSON at sixteen levels. Unknown fields,
duplicate keys, invalid Unicode, non-finite values, old versions, code, raw input,
selectors and unknown operations fail closed. The simulator verifies a current
explicit grant and HMAC before parsing its private command payload. There is no
unauthenticated mission health endpoint. Public local diagnostics disclose no
private mission status.

The guest durably reserves a command before executing its fixed handler. Duplicate
start/approval operations never re-execute. A matching read-only request may return
one of at most 32 cached responses; it cannot grant input. Each grant allows 256
normal requests and eight reserved emergency-stop requests. The ledger caps normal
request records at 8,192 with eight reserved stop slots; reaching a cap requires
operator inspection/archive/re-pairing, never silently deleting replay guards.
The host permits four normal requests in flight and gives stop a separate priority
path. Frames cannot call imports/eval/exec, shell, files, raw input or arbitrary
method names. The UI dispatch map is a fixed application-owned list.

A mission challenge binds the exact existing scheduler consent, workspace/session,
plan fingerprint, observation reference, display digest, nonce, expiry and original
mission. It is consumed in a guest transaction before continuation. A host message
cannot write the effect journal or classify an arbitrary effect as safe. Current
remote challenges authorize only the fixed **simulation mission**, not live
per-action approval. Real exact-effect review remains in the existing Phase 3
controller and must be integrated/qualified separately.

## Privacy and storage

The host registry persists only one descriptor: opaque workspace ID, safe label,
manual backend and PENDING qualification. It stores no endpoints, secret, token,
screenshot, raw goal, action history or journal copy. Current status is a bounded
RAM projection marked stale after disconnect. Preview is one bounded synthetic
PNG with guest-origin geometry and capture metadata, requested explicitly,
consumed from the host buffer and not stored in SQLite. Preview coordinates are
read-only metadata and never become input commands.

Guest control tables contain opaque IDs, request fingerprints, fixed operation
names, session associations and challenge metadata. RAM pairing keys/codes, raw
frames, user documents, typed content, provider errors and model reasoning are
absent from these tables and bounded diagnostic events. Keys/codes are excluded
from object repr. Arbitrary transport exceptions become fixed error categories.
No analytics/cloud tracing or credential transfer is introduced.

The simulation key disappears on process exit. Real identity persistence is
**unimplemented**, not a plaintext JSON substitute. The selected native design
requires Windows-protected per-user credential material with restricted ACLs,
explicit out-of-band pin verification, expiry and revocation. Until reviewed and
implemented, native transport always refuses connection, even if passed a
purported PASS object.

## Risks, mitigations and residual limits

| Risk | Portable mitigation / remaining native work |
| --- | --- |
| Identity spoofing | Explicit fake identity verification, HMAC and instance checks; mutual TLS and OS keys remain pending |
| Replay/duplicate task | Durable request reservation and one-use challenge; no retries of uncertain input |
| Hostile model/web content | Not an accepted control payload; existing deterministic Phase 3 policy remains unchanged |
| Stale scene/DPI/provider | Binding changes revoke challenges and stop; actual Windows capture/session sensors remain pending |
| Network partition/lost stop | Guard checks deadline on every stage and watchdog disarms; unacknowledged host stop stays unconfirmed |
| Unknown cleanup | Quarantine prevents admission; “stop confirmed” never substitutes for effect reconciliation |
| VM clone/snapshot | Fresh instance identity at simulator boot and explicit restore invalidation; actual live-memory snapshot detection is unqualified |
| Shared folder/VM escape | No automatic sharing or provisioning; administrator must harden and patch host/guest |
| Secret exfiltration | No real data/model egress; future native endpoints require scoped routing and guest-local credential storage |
| User confusion | Explicit simulation labels, personal default, no background mode restoration, no physical cleanup claim |

A VM is not a perfect containment guarantee. Simulation tests do not establish
TLS implementation security, Windows isolation, independent physical pointers,
real stop latency, protected capture, or safe RDP-disconnected operation.
