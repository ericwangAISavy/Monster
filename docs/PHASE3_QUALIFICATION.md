# Phase 3 qualification and acceptance evidence

**Status: implemented, native Windows qualification PENDING.** No real mouse,
keyboard, external submission, account workflow or LAN model was used in CI tests.
Phase 2 native qualification remains pending as well.

## Automated evidence

Pre-change mandatory prerequisite: **586 passed, 3 skipped in101.67s** at6229be6.
First full Phase 3 suite: **627 passed, 3 skipped in91.10s**.
Final suite: **631 passed, 3 skipped in94.36s**; 45 new tests over the baseline.
Three skips are the existing native Windows/UIAccess checks. These counts establish
portable logic and fake-controller behavior, not native execution reliability.

Exact commands, from repository root:

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run pytest -q
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff check src tests
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff format --check src/monster/mission tests/mission/test_effects_policy.py tests/mission/test_effect_transactions.py tests/mission/test_reconciliation_runtime.py tests/test_phase3_dispatch.py
UV_CACHE_DIR=/workspace/.cache/uv uv run python -m compileall -q src
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run monster --smoke-test
git diff --check
```

Frozen sync, lint, formatting and byte compilation passed. Offscreen startup exited
0 with the expected unsupported tray/raise messages. Async/thread tests used the
normal execution escalation because the restricted sandbox blocks thread-to-loop
wakeups. No new dependencies or uv.lock changes were needed.

Attempted `uv run --no-sync python -m mypy src`: **not available**, `No module named
mypy`. This repository does not configure/install mypy; no static-type-check pass
is claimed and no dependency was added solely to create such a claim.

The actual SupervisedController/Phase 2 bridge is exercised with fake providers,
fake screenshots and fake input. One test commits approval and dispatch intent
before the sole fake native transaction; another injects a database failure at the
intent write and confirms **zero input transactions**, no success and a paused task.
Seven real subprocess exits cover pending approval, approval, intent, dispatch,
receipt and observed success/failure. Recovery never replays ambiguous effects.
Two independent SQLite writers race one approval: exactly one commits consumption.
An injected audit failure rolls back consumption, intent and audit together.

Independent review identified provider-diagnostic redaction bypass, uncertain
navigation continuing inside a subtask, and global reconciliation/error-display
issues. Each has a failing regression followed by a fix in the passing suite.
Mission diagnostics now discard content before provider callbacks can retain it;
unresolved workspace actions deny both new preparation and consumption; global
review retains all missions and storage errors are reported separately from empty
results. Unsupported scope fields/malformed consent also fail closed.

## Required scenario coverage

| Spec IDs | Evidence / tests |
| --- | --- |
| P3-01..06 | `test_effects_policy`: policy matrix, inactive/malformed consent, no scoped enable flag, model/page cannot downgrade, live external denial / simulated consequence review. |
| P3-07..10 | Fingerprint/content mismatch, wrong UI review, expiry/revocation, changed generation/workspace/target/scene/lease/active scope. |
| P3-11..12 | `test_effect_transactions`: two-writer consumption, audit rollback, busy DB; `test_phase3_dispatch`: injected journal failure sends zero fake input. |
| P3-13..15 | Real subprocess exit at seven action boundaries; pending/approved become not_dispatched; intent/started/receipt become uncertain. |
| P3-16..18 | Inserted receipt remains awaiting_observation; referenced visual result may succeed; missing reference rolls back; uncertain remains blocked. |
| P3-19..20 | Success preserved across restart; Phase 1/2 checkpoint tests plus action unresolved/start/continue guards; uncertain prior navigation blocks in-attempt preparation. |
| P3-21..23 | Human attestation source preserved, human rejection provenance, approval revocation and existing stop/approval-cancel tests. |
| P3-24..25 | Untrusted model/page labels cannot change classification; extra permission fields denied; unsafe text not equal to fixed objective is unknown; provider diagnostics regression excludes denied password-like proposal text; journal contains HMAC only. |
| P3-26..28 | Existing node replay/duplicate/stale-generation tests; schema migration/atomic DDL tests updated for v3; entire standalone/UI/provider/worker suite green. |
| P3-29..30 | Existing queued-stop/shutdown/worker tests plus controller refusal before durable ack, busy/error/rollback paths and fixed journal-unavailable notices. |

No live external commit was used to test approvals. Consequential policy is tested
as data only; real commits remain denied. Observe-only reconciliation intentionally
uses a human handoff because Phase 2 has no independently qualified read-only
capture adapter. This is the explicit allowed fallback, not an omitted input tool.

## Native Windows manual procedure — NOT RUN

Use an unlocked disposable Windows 11 VM/account, known-working selected vision
model, non-sensitive Notepad data, standard privilege and a person present. Do not
use production websites/accounts, payment pages or external submission flows.

```powershell
git pull --ff-only
uv sync --frozen
uv run monster
```

1. Verify ordinary standalone supervised input still behaves as before. Confirm
   the independent Ctrl+Alt+X shortcut and cleanup using existing native guides.
2. Open an empty disposable Notepad document and focus its editor. Select the
   existing experimental two-draft mission. Use Ctrl+Alt+Y for mission review so
   the pill does not steal target focus. Each text action gets a separate exact
   action card, risk, fingerprint prefix and 60-second review.
3. Authorize only the expected A sentence. Inspect the real insertion, confirm
   final result and cleanup. Then authorize B with fresh mission and action review.
   Confirm exactly one insertion per sentence, no replacement and truthful outcome.
4. Reject/Edit/Pause an action; expire another. Confirm no dispatch and no reused
   grant. Enter, unclassified clicks and other unsupported operations should be
   refused; do not loosen policy to force the demo through.
5. Change focus/window geometry/scene after approval. It must reobserve/refuse and
   require fresh review. Record actual behavior, not a model claim.
6. In a test build/disposable VM, inject a journal error before intent commit.
   Confirm zero worker input. Terminate after intent/receipt, restart, and verify
   uncertainty with no replay. Never perform a real external send for this test.
7. Tray → **Mission effects · observe-only review…**. Inspect the intended effect
   yourself; attest only what can independently be established. Confirm the journal
   labels it human_attested and never restarts input. Leave unknown results unresolved.
   Verify storage errors do not display “no unresolved effects.”
8. Trigger Ctrl+Alt+X during review, model wait and bounded execution. Confirm no
   subsequent input and inspect owned keys/buttons if cleanup is unconfirmed.
   Unknown cleanup must keep the gate quarantined; attestation must not clear it.
9. Inspect exported mission metadata/database for raw typed bodies, credentials,
   screenshots or keys. Mission screenshots are not retained in diagnostics.
   Keep private screenshots out of reports; do not create real credentials to test.
10. Record OS/source/Python/model/runtime/digest, hardware, scaling, exact commands,
    actual drafts/attempts, wrong-target/duplicate counts, latency, stop/cleanup and
    intervention outcomes. Distinguish model/grounding failures from executor errors.

A screenshot/model success or an inserted receipt is not external completion.
Capture/foreground correctness, actual stop latency and physical held-input cleanup
remain unverified on this Linux workspace. Above-Start UIAccess qualification is
separate and still pending. Do not describe these simulations as production autonomy.

## Narrow Phase 4 proposal

First qualify Phases 2/3 natively. Then run one bounded, harmless multi-subtask pilot
with explicit time/action budgets, fresh review, human takeover and measured recovery.
Do not add external commitments, strategic multi-agent planning, new models/providers
or unattended execution as part of that pilot. Richer task-level reconciliation and
operator-controlled audit archival may be separate follow-ups.
