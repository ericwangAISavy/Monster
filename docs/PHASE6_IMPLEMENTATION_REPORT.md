# Phase 6 portable implementation report

Implemented the prompt's portable-only path while preserving earlier uncommitted
Phase 5 and shortcut work. Actual live TLS/VM integration is pending the explicit
ADR006 design-review gate and prerequisite/native Windows qualification. This is
not a completed physical independent-workspace release.

## Audit and scope

Branch `work`, HEAD `338ee94d5aed9262a5b9a0123a66e88783065a8a`. Phases 1–5 code
and interfaces were present. Baseline: **766 passed, 3 skipped**, 101.48s.
Windows Phases 2–5: PENDING. Python 3.12.14, existing frozen uv environment and
CI conventions; no new dependency, lockfile change, administrator command, VM,
network listener or model connection was introduced.

The source prompt §16 Slice A requires: “Reviewer approves the design before
network or input integration.” Its §22 deliverable 4 explicitly permits a
portable-only result while native qualification is blocked. The concrete proposed
live topology and trust boundaries are recorded in
`PHASE6_WORKSPACE_ARCHITECTURE.md` for that review. FakeTransport is in-memory
with RAM HMAC, **not TLS**. Native transport and input entry points always refuse.

## Changed modules

| Module | Concrete behavior |
| --- | --- |
| `workspaces/contracts.py` | Strict schema 1 descriptors, binding, capabilities, status, mission challenge, commands/replies and bounded synthetic previews |
| `workspaces/protocol.py`, `pairing.py` | Bounded authenticated simulation frames, explicit fake console verification, expiry/revocation, no durable credentials |
| `workspaces/registry.py` | One host descriptor only; no mission DB, endpoint, screenshot or key in preferences |
| `workspaces/guest_service.py` | Existing guest-role MissionCoordinator and ledger; request reservation, challenge consumption, scoped handler allowlist, boot/reconnect/watchdog/session guards, durable quarantine |
| `workspaces/host_client.py` | Monotonic read-only host projection, human response forwarding, priority stop, unconfirmed ACK handling and ephemeral preview |
| `workspaces/fake_transport.py`, `simulation.py` | Deterministic network/session/identity/crash faults and four CLI scenarios |
| `workspaces/runtime.py` | Async guest DB/link work on a worker thread; Qt signals carry only bounded status/review/preview |
| `workspaces/transport.py` | Explicit native-unavailable gate; no plaintext fallback or force override |
| `guest.py` | Restricted explicit module entry, disarmed diagnostics and simulation only |
| `ui/workspace_settings.py`, `app.py` | Temporary Workspaces settings, explicit selector, fake pairing verification, existing-pill review, stop/status/preview; personal default unchanged |
| `mission/scheduling.py` | Additional optional workspace guard before any stage; absent in personal mode |
| `mission/repository.py` | Additive schema 6 tables and terminal association/challenge retention |
| `mission/runner.py` | Input-receipt-only fake result stays uncertain without visual evidence |

Schema expectations in prior migration tests were advanced to version 6; assertions
about earlier execution behavior were preserved. Tests use no live LAN server,
external site, real input or Windows management API. No Phase 7 workflow was added.

## Measured simulation outcomes

All four documented CLI scenarios executed successfully and their JSON results
were independently asserted:

| Scenario | Guest state | Verified stages | Unresolved | Synthetic delegations | Native input |
| --- | --- | --- | --- | --- | --- |
| pair_and_status | idle | 0 | false | 0 | 0 |
| partition_pause | paused | 0 | true | 1 | 0 |
| crash_reconcile | paused | 0 | true | 1 | 0 |
| six_stage_fake | completed | 6 | false | 6 | 0 |

These are fake outcomes, not proof of real Notepad content, TLS security or two
independent physical Windows sessions. An input receipt without visual outcome
is explicitly uncertain. A lost stop ACK remains unconfirmed on the host even
when the simulated guest has disarmed. Unknown cleanup persists across guest
process restart and blocks new admission.

## Preserved safety invariants

Only the guest role's existing coordinator owns missions, budgets and effects;
the host never rewrites their history. No control frame contains a raw input,
shell, code, file or generic RPC method. Exact workspace/instance/session/display/
provider binding, request IDs, monotonic sequences, expiry and one-use challenges
are checked before continuation. A new host/guest instance, session event or
reconnect does not authorize input. Old requests cannot start a duplicate mission.
Network ACKs are not visual proof. Two simultaneous mission owners are refused.

Personal tasks retain their original controller. Independent-workspace UI commands
never submit to it, and opening the pairing/settings surface during personal
input requests cleanup first. The host cursor is never assigned guest coordinates.
The actual independent Win32 stop/capture/input path remains disconnected until
separately reviewed and qualified inside the guest.

## Native release work remaining

1. Review ADR006 before live network or input integration.
2. Implement/review the selected guest-initiated pinned mutual-TLS link and
   Windows-protected per-device identity storage, using private networking only.
3. Connect the existing guest Qt capture/controller/worker path with exact Phase 3
   effect review; keep qualification/focus/session/worker gates authoritative.
4. Complete Phase 2–5 prerequisites and the Phase 6 manual checklist with actual
   host/guest builds, configuration digests, observations, stop/cleanup evidence,
   session limitations, failure counts and operator review.
5. Measure real host responsiveness, guest capture/approval/inference latency,
   resource usage, independent stop, false completion and duplicate attempts.

No PASS record, live-memory snapshot rollback detection, physical pointer
independence, RDP-disconnected capture, OS credential integration or LAN pairing is
claimed. See `PHASE6_VM_SETUP.md`, `PHASE6_SECURITY_MODEL.md`,
`PHASE6_OPERATIONS_AND_RECOVERY.md` and `WINDOWS_PHASE6_QUALIFICATION.md`.

## Exact verification results

Commands ran in `/workspace/Monster` with `UV_CACHE_DIR=/workspace/.cache/uv`.
Linux Qt commands additionally used `QT_QPA_PLATFORM=offscreen`,
`XDG_CACHE_HOME=/workspace/.cache` and `XDG_CONFIG_HOME=/workspace/.config`.

| Command | Outcome |
| --- | --- |
| Baseline `uv run --frozen pytest -q` | 766 passed, 3 skipped, 101.48s |
| Integration `uv run --frozen pytest -q tests/workspaces tests/mission` | 298 passed, 42.62s before later fault/quarantine additions |
| Final full `uv run --frozen pytest -q` | **857 passed, 3 skipped**, 112.90s |
| Focused `uv run --frozen pytest -q tests/workspaces` | **91 passed**, 7.79s |
| Final workspace UI check after readable-height refinement | **6 passed**, 2.94s |
| `uv run --frozen ruff check src tests` | Passed |
| `uv run --frozen python -m compileall -q src` | Passed |
| `uv run --frozen monster --smoke-test` | Passed, expected offscreen tray/raise warnings |
| `uv run --frozen python -m monster.guest --help` / `--diagnose` | Passed, no native worker/listener/credential loading |
| Four `python -m monster.workspaces.simulation --scenario ...` commands above | Passed; JSON behavior independently asserted |
| `git diff --check` | Passed |

The three skips are the existing native Windows UIAccess tests in
`tests/test_overlay_windows.py`; Linux does not supply those APIs. The initial
full-suite collection found a basename collision with `tests/test_ui.py`; the new
UI test file was renamed `test_workspace_ui.py`, after which collection and full
execution passed. No tests were disabled to resolve it. The final full run preceded
a display-height-only settings refinement, separately validated by all six Qt
workspace UI tests. No mypy/type-check configuration is present or claimed.

The Workspaces surface was rendered and inspected offscreen. No real pairing
credentials, user screenshots, model data or private desktop content were used.
Changes remain uncommitted for review. No VM/OS provisioning, publishing,
deployment, network-policy change or live transport integration was performed.
