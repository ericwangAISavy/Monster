# Phase 7 prerequisite gap — implementation stopped

Audit: branch `work`, HEAD
`338ee94d5aed9262a5b9a0123a66e88783065a8a`. Existing uncommitted Phase 5/6 and
shortcut changes are preserved. No AGENTS.md was found. CI, pyproject.toml,
uv.lock, strategic/workspace source, tests and qualification documents were inspected.

The supplied `CODEX_MONSTER_V3_PHASE7_PROMPT.md` §0 requires Phase 6 to provide
“one paired isolated Windows guest with screenshot/input execution inside the
guest, authenticated host control, local stop, session-loss fail-safe, and
authoritative guest-side mission/effect database.” It instructs: “If any
prerequisite is missing or critically broken: STOP before Phase 7 implementation.”

| Prerequisite | Current evidence | Result |
| --- | --- | --- |
| Phases 1–4 | Existing coordinator/graph/repository, Qt bridge, independent worker, effects/policy/reconciliation, scheduler and regression tests | Present |
| Phase 5 | `mission/strategic/{contracts,catalog,planning,service}.py`: typed plans, admission, evidence evaluation and constrained revision | Present as the documented bounded simulation implementation |
| Phase 6 portable work | `workspaces/{contracts,pairing,guest_service,host_client,fake_transport,runtime}.py`, workspace UI, fault tests and guest-role ledger | Present |
| Phase 6 live authenticated link | `workspaces/transport.py:9`: `connect_native` always raises; simulation is RAM HMAC over an in-memory link, not encrypted TLS | **Missing implementation** |
| Phase 6 actual guest execution | `workspaces/guest_service.py:72` accepts only `FakeGuiSubtaskRunner`; `guest.py` exposes diagnostics/simulation and refuses native operation | **Missing integration** |
| Phase 6 actual capture/independent guest stop | No native capture/controller/worker bootstrap in the guest entry point; advertised readiness is deliberately false | **Not connected or qualified** |
| Native Phases 2–6 | Existing qualification documents explicitly say PENDING | Not qualified |

The blocker is more than unavailable native test evidence. The Phase 6 production
transport, OS-protected identity persistence and actual guest executor wiring do
not exist yet. Phase 7 permits portable tests before native qualification, but
that permission does not replace the separate requirement for the prerequisite
implementation itself. Successful fake runs cannot satisfy it.

## Required remediation

1. Review ADR006 (`PHASE6_WORKSPACE_ARCHITECTURE.md`), as the supplied Phase 6
   prompt explicitly requires reviewer approval before network/input integration.
2. Complete/review the guest-initiated authenticated encrypted transport and
   Windows-protected pairing identity storage on the Phase 6 work.
3. Wire the existing guest Qt capture/controller/independent worker and exact
   effect approvals into that guest runtime, retaining guest-only authority,
   session/workspace binding, stop, quarantine and no automatic replay.
4. Verify the completed implementation and record honest qualification status.
   Real Phase 7 pilots still require relevant Windows/guest qualification PASS;
   portable Phase 7 work may then proceed under its stated gates.

Only local branch `work` was present in inspected refs. No qualifying Phase 6
completion branch or PR was identified or supplied; no merge target is invented.
Complete these gaps on the Phase 6 work, or provide the actual reviewed branch/PR
containing them, before starting Phase 7.

No Phase 7 implementation, workflow registry, pilot stub, earlier-phase feature,
new dependency or gate relaxation was added during this audit. This report is the
only new repository file for this request.

## Verified baseline

From `/workspace/Monster`, with `UV_CACHE_DIR=/workspace/.cache/uv` and Linux Qt
settings `QT_QPA_PLATFORM=offscreen`, `XDG_CACHE_HOME=/workspace/.cache`,
`XDG_CONFIG_HOME=/workspace/.config`:

- `uv run --frozen pytest -q`: **857 passed, 3 skipped**, 117.03s.
- `uv run --frozen ruff check src tests`: passed.
- `uv run --frozen python -m monster.guest --diagnose`: reports no live listener,
  input worker or verified capture; native input disabled, qualification PENDING.
- `git diff --check`: passed.

There is no failing baseline regression. The missing prerequisite is the live
Phase 6 contract: existing tests intentionally verify native connection refusal
and fake-only execution (`tests/workspaces/test_control.py`). Three Windows
UIAccess tests remain skipped on Linux. Passing portable tests does not establish
an authenticated physical guest or permit bypassing the Phase 7 dependency gate.
