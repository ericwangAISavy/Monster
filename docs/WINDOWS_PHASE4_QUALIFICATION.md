# Windows Phase 4 qualification — PENDING

**No native Windows Phase4 pilot was executed.** Phases2/3 checklists also remain
pending. Linux/offscreen fake tests are not qualification evidence. This release
must not be described as production or unattended autonomy.

## Actual portable validation

Baseline:631 passed,3 skipped in81.16s. First integrated suite:648 passed,3 skipped
in99.14s. Subsequent full suite:662 passed,3 skipped in96.07s. The three skips are
existing Windows/UIAccess checks, not three successful native tests.

Commands executed from repository root:

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run pytest -q
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff check src tests
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff format --check src/monster/mission tests/mission/test_phase4_scheduling.py tests/mission/test_phase4_restart.py
UV_CACHE_DIR=/workspace/.cache/uv uv run python -m compileall -q src
UV_CACHE_DIR=/workspace/.cache/uv QT_QPA_PLATFORM=offscreen uv run monster --smoke-test
UV_CACHE_DIR=/workspace/.cache/uv uv run python -m monster.mission.pilot --directory /tmp/monster-phase4-delivery-success --scenario success --approve-simulation
git diff --check
```

Frozen sync, lint, formatting, byte compilation and startup passed. Startup exited0
with the expected offscreen tray/raise warnings. No dependencies/lockfile changed.
Mypy is not configured/installed; no static-type-check pass is claimed.

The explicit CLI success run reported **6/6 fake evidenced stages, six simulated
input transactions, zero native input transactions**, with simulated provenance.
This is fixture correctness, not GUI success rate or model performance. Subprocess
fixtures also run pause/restart, pure timeout, ambiguous dispatch, exhausted budget
and observe-only scenarios. An actual `os._exit(79)` after subtask intent retained
its full reservation across reopen and never called the replacement runner.

A real schema3 SQLite/checkpoint fixture copy (new additive tables removed) migrated
and resumed its original approval with preserved audit history. Two fake missions
shared the coordinator without shared counters. Unknown costs remain charged,
known counters remain monotonic, and schema/fault/Qt regressions from prior phases
continue to run. Final release test count is appended below.

## Acceptance coverage map

| IDs | Evidence |
| --- | --- |
| P4-01..05 | Full baseline; template/strict budget contracts; existing DAG cycle/unknown dependency tests; exact/expired/digest/catalog consent regressions. |
| P4-06..08 | Existing Phase2 one-owner/session-lock tests; concurrent simulations; six-stage graph test, once each and ordered. |
| P4-09..13 | Phase3 receipt/evidence tests; missing-cleanup/provenance regression; journal unresolved guards; real process crash with retained charges; boundary-pause/restart skips A. |
| P4-14..15 | Pure fault retries/no-progress cap; exhausted per-subtask planning cap prevents handoff; post-handoff timeout remains uncertain without retry. |
| P4-16..21 | Snapshot-barrier pause race; existing immediate-stop/takeover/Qt cancellation tests; fresh consent after restart, provider/scene bindings, expired/native qualification guards. |
| P4-22..25 | Action/model/start/active envelope tests; wall/clock-rollback guards; persisted overflow; hard two-no-progress rule even with three configured attempts. |
| P4-26..28 | Existing Phase3 new-ID/unresolved protections and queued stale callbacks; no fallback; runtime shutdown tests and fake Phase4 facade lifecycle. |
| P4-29..34 | Full Phase3 deterministic policy/redaction/DB-before-dispatch suite unchanged; fixed catalog only; progress/report/database contain no canned bodies or screenshots. |
| P4-35..39 | Schema3 SQLite+checkpoint migration; future-schema rejection; Qt-generation/duplicate callback regressions; simulated provenance; restart counter/reservation tests. |
| P4-40 | All six CLI scenarios plus the end-to-end scheduling/fault matrix. |

These IDs combine new tests with existing qualified-in-code guards; they do not
mean each item was exercised against Windows. Hypothesis is not a project dependency;
no unrelated property-testing framework was added. Deterministic DAG, concurrency,
clock, cancellation and crash fixtures cover the relevant invariants.

## Gate order for a native pilot

1. Complete `WINDOWS_PHASE2_QUALIFICATION.md` on the actual Windows account/VM.
2. Complete `PHASE3_QUALIFICATION.md`, including exact effect review and crash recovery.
3. Review failures, fix blockers and retain non-sensitive evidence references.
4. Only then prepare operator-reviewed qualification metadata for the **current
   source revision, selected provider configuration and interactive workspace**.
5. Run the Phase4 pilot with a human present on an unlocked disposable Windows11
   standard-privilege desktop. Never use production accounts or external actions.

The live gate expects a strict `NativePilotQualification` JSON record: schema_version1,
phase2/phase3 status PASS, operator_review_ref, phase2_evidence_ref, phase3_evidence_ref,
workspace_id (`interactive-desktop`), configuration_digest, source_ref and future UTC
epoch expires_at. Populate PASS **only from actual reviewed native evidence**.
There is deliberately no prefilled PASS example or simulation-generated certificate.
The configuration digest is `mission.scheduling.provider_binding(saved_config)`;
the source revision and file fingerprint are in Monster's diagnostics. This local
record does not replace operator judgment, secret management or real testing.

After those prerequisites, explicit opt-in is:

```powershell
git pull --ff-only
uv sync --frozen
uv run monster --phase4-qualification C:\Private\Monster\reviewed-qualification.json
```

A missing/invalid record leaves the new pilot disabled. The coordinator also checks
Windows, a real experimental adapter, matching source/configuration/workspace and
expiry before admission/resume/transitions. Simulation and observe-only need no
qualification file and never create a native runner. Existing two-stage behavior
remains a separate, still-experimental path.

## Native checklist — NOT RUN

- Confirm normal tiny GUI, overlay-free screenshots and standalone/two-stage input.
- Focus an empty disposable Notepad document. Tray → **Phase4 · qualified native pilot…**.
- Review the exact six-stage scope, caps, screenshot destination/model and expiry.
  Consent permits orchestration only. No Send/Enter/unknown click is auto-approved.
- Approve required effect cards; verify each known marker once, with earlier text
  preserved. At the final confirmation, inspect all six sections, not just F.
- Check requested progress: verified/active/remaining stages, charged vs known
  counts, no unverified optimistic advancement, cleanup completed between stages.
- Request **pause after stage** during an active subtask. Only that bounded stage
  may finish. No next stage. Separately test immediate Ctrl+Alt+X and manual takeover.
- Resume explicitly with fresh consent/scene/lease. Move Notepad/change focus or
  display geometry; no old coordinates or authorization may be reused.
- Restart after a clean stage boundary: completed stages must not repeat. Restart
  after an ambiguous intent/receipt: reconciliation required, no retry/new-ID bypass.
- Disconnect Ollama, expire consent, exhaust a small global envelope and inject a
  DB failure before action intent. UI must remain responsive; no fallback/extra input.
- Verify exclusive ownership with a second Monster instance. Inspect held modifiers/
  buttons whenever cleanup is unknown. Attestation must not clear quarantine.
- Inspect metadata/report privacy. Do not capture credentials or personal documents.
  Screenshots require consent and never count as evidence merely because capture ran.
- Record OS/build/Python/source fingerprint, hardware/GPU, model/digest/runtime,
  DPI/monitor bounds, commands, drafts/attempts, wrong targets/duplicates, timings,
  interventions, stop latency and cleanup proof. Separate model vs executor defects.

The OS/Qt event loop cannot provide hard real-time preemption under a stall. A
measured timing/envelope violation must stop incomplete, not be hidden. Actual
median model/capture/input latency and native duplicate counts remain unmeasured
until this checklist runs. Above-Start UIAccess is a separate qualification gate.

## Next phase boundary

Phase5 should introduce structured strategic planning/reasoning inside an admitted
scope and evaluate time to evidenced progress. It must not weaken exact approvals,
expand task tools, silently change models or enable unattended external commitments.

**Final release validation:663 passed,3 skipped in93.86s in the full suite** (32 new tests
over Phase3). The consent-card regression checks that the displayed caps match
the admitted resource envelope. Native qualification remains PENDING.

After the final compact reason wording, the focused UI suite also passed:26 tests in0.96s.
