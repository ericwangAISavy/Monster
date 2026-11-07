# Phase 6 manual VM setup

**Native TLS pairing, Windows credential integration and VM input are not enabled
by this change.** This is the manual preparation/qualification guide for the
reviewed native integration, not an installer or claim that the portable simulator
is a usable VM control product. Complete ADR006 design review first.

## Supported target and resource planning

The first intended target is one manually provisioned Windows 11 x64 guest on a
Windows 11 Pro/Enterprise Hyper-V host. Windows Home does not supply Hyper-V.
A separate guest OS needs appropriate licensing; a host license must not be
assumed to cover the guest. No installer, administrator elevation, BIOS, account,
firewall or physical adapter modification is performed by Monster.

Check hardware virtualization and available host capacity with the administrator.
A starting qualification configuration is a generation-2 VM with secure boot,
virtual TPM, four vCPUs, 8–16 GB RAM and at least 100 GB fast virtual disk, subject
to Windows requirements and workload. These are starting points, not guarantees.
Keep enough host headroom to use personal applications normally. One guest and one
input owner are supported; GPU passthrough and cloud/multi-VM backends are outside
this phase. Initially use the existing separately managed LAN inference server.

Official references:

- https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v
- https://learn.microsoft.com/en-us/windows/whats-new/windows-11-requirements
- https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/virtual-machine-connection
- https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/use-local-resources-virtual-machine-connection

## Administrator-owned preparation

1. Manually create a disposable supported Windows guest and install updates.
2. Use a dedicated, logged-in standard user, not an administrator account.
3. Disable unnecessary shared folders, clipboard, drive, printer and device
   redirection. Minimize host resources visible to the guest.
4. Create a host-only/private network. Administrator-owned firewall rules must
   permit only the chosen host control port between that host and guest and the
   approved inference endpoint. There is currently **no live Monster control
   listener** to configure or expose. Never expose it to the Internet.
5. Install Python 3.12+, uv and the exact reviewed Monster source inside the guest.
   Run `uv sync --frozen` in that checkout. Do not copy host credential files.
6. Open an unsaved disposable Notepad document. Do not use production accounts,
   sensitive documents, real credentials or external submissions for qualification.
7. Start with a basic VM console session. Enhanced session/RDP, resizing,
   disconnects, lock screens, UAC and secure desktops require separate tests.
8. Capture the source/package hashes, guest/session identity, display geometry,
   DPI, Windows build, network routing, model ID/digest and inference destination.

## Commands available now

Host UI (personal mode remains the default):

```text
uv run monster
```

Open tray → **Workspaces…**. Register one guest, explicitly select **Independent
Workspace · simulation**, verify the *simulated* pairing dialog, then start the
six-stage guest simulation. Approve its guest-issued challenge in the existing
pill. Preview requests show one synthetic image only. Stop confirmations describe
the simulated guest, never a physical Windows input worker. Mode selection and
pairing are not restored automatically at launch.

Restricted guest entry point:

```text
uv run python -m monster.guest --help
uv run python -m monster.guest --diagnose
uv run python -m monster.guest --simulate six_stage_fake
```

`--diagnose` starts no worker or listener and loads no live credentials. It reports
native qualification PENDING. There is no `--force`, `--native`, plaintext listener
or qualification-file override. A `monster-guest` console script was not added;
the supported entry point is the explicit Python module above, requiring no
package/lockfile change.

Portable fixtures (no VM, sockets, LAN server or real input):

```text
uv run python -m monster.workspaces.simulation --scenario pair_and_status
uv run python -m monster.workspaces.simulation --scenario partition_pause
uv run python -m monster.workspaces.simulation --scenario crash_reconcile
uv run python -m monster.workspaces.simulation --scenario six_stage_fake
```

A temporary directory is used unless `--directory` is supplied. The guest role
owns the SQLite journal in the simulation; host projection never receives a
writable copy. In the host UI the fixture directory lives beneath
`guest-simulation/<opaque-workspace-id>` in Monster's local configuration directory.
That is a test guest role running in the same process, **not an isolated Windows VM**.

## Native integration gates

After ADR006 review, implement/review the guest-initiated pinned mutual-TLS
transport and Windows-protected identity storage. Verify both fingerprints via
visible host/guest consoles, require local guest pairing mode, and test expiry,
rotation/revocation and single-controller ownership. No trust-on-first-use or
pre-shared sample password is acceptable. This ceremony is specified but not
live-enabled by the portable simulator.

Complete Phases 2–5 Windows qualification first, then Phase 6's disposable native
checklist. Only a separately reviewed native bridge may start the existing guest
Qt controller, capture service and independent worker inside the guest session.
The host must never click through VMConnect/RDP or send raw mouse/keyboard RPCs.
Unqualified/missing/stale evidence must continue to disable native input.
