# Phase 5 implementation report

Implemented the portable strategic planning and six-stage simulation path on the
existing `work` checkout. Base HEAD is
`338ee94d5aed9262a5b9a0123a66e88783065a8a`; changes are uncommitted. The earlier
Ctrl+N/shortcut-consistency fix is retained. Phase 1–4 implementation prerequisites
were present, and the baseline was 667 passed / 3 skipped. Native Phase 2–5
qualification remains **PENDING**.

## Changed modules

| Module | Responsibility |
| --- | --- |
| `mission/strategic/contracts.py` | Typed goals, criteria, roles, limits, proposals, revisions, digest consent, evaluations and usage metadata |
| `mission/strategic/catalog.py` | Application-owned simulation descriptors, DAG/coverage/scope validation, deterministic compilation into Phase 4 tasks |
| `mission/strategic/planning.py` | Provider-neutral protocol, canned/adversarial fake planner, bounded text-only Ollama adapter, strict parser and private-intake checks |
| `mission/strategic/service.py` | Atomic admission, durable counters and revision ancestry/proof, concurrency/epoch guards, evidence evaluation and bounded context |
| `mission/strategic/pilot.py` | Explicit six-stage CLI simulation, pre-dispatch alternative, restart and optional saved Ollama configuration |
| `mission/coordinator.py`, `graph.py`, `scheduling.py` | Reuse existing scheduler/checkpoints, strategic admission guard, milestone/goal evaluation, full-state recovery before the first checkpoint |
| `mission/repository.py` | Additive schema 5 migration and terminal retention |
| `mission/runtime.py` | Existing Qt worker-thread planning/review/revision facade; no new input runner |
| `ui/strategic_review.py`, `app.py` | Temporary readable plan review, opt-in tray actions, criterion-aware progress and stop invalidation |
| `tests/mission/test_phase5_strategic.py`, `test_phase5_ui.py` | 99 portable planning, safety, fault, privacy, migration, provider-contract and Qt tests |

Existing migration tests now expect schema 5 and continue testing atomic rollback
and preservation. No dependencies, lockfiles, native input implementations,
Phase 3 policy rules, or default supervised settings changed for Phase 5.

## Preserved invariants

The model supplies structured proposals, never authority or executable native
instructions. Only the existing scheduler delegates work. This phase requires the
exact fake runner and rejects dynamic native execution even on Windows. Uncertain
input/cleanup blocks planning. Verified effects remain immutable; stable objective
IDs and parent/digest checks prevent rename-to-retry. All changed plans require
fresh review and exact scheduler consent. Revisions do not reset global budgets,
stop state, deadlines, or failures. Late/cancelled responses cannot replace a plan.

The earlier Phase 4 pure-preflight path counted one failure in both `note_failure`
and settlement. Settlement now preserves the already-counted failure instead of
counting it a second time. Two actual failures still exhaust the limit. Existing
Phase 4 regression tests remain green.

## Executed validation

Commands ran in `/workspace/Monster` with Python 3.12.14, the frozen uv environment,
and `UV_CACHE_DIR=/workspace/.cache/uv`. Linux GUI checks used
`QT_QPA_PLATFORM=offscreen`, `XDG_CACHE_HOME=/workspace/.cache` and
`XDG_CONFIG_HOME=/workspace/.config`.

| Command | Outcome |
| --- | --- |
| Baseline `uv run --frozen pytest -q` | 667 passed, 3 skipped, 96.44s |
| Prior mission regressions during integration | 132 passed |
| Full `uv run --frozen pytest -q` | **766 passed, 3 skipped**, 110.40s |
| Final focused `uv run --frozen pytest -q tests/mission/test_phase5_strategic.py tests/mission/test_phase5_ui.py` | **99 passed**, 8.85s |
| `uv run --frozen ruff check src tests` | Passed |
| `uv run --frozen python -m compileall -q src` | Passed |
| `uv run --frozen monster --smoke-test` | Passed; expected offscreen tray/raise warnings |
| `git diff --check` | Passed |

The full run preceded final revision-provenance metadata refinements; the final
focused 99-test run validates those refinements. All three skips are in
`tests/test_overlay_windows.py` because native Windows UIAccess APIs are required.
No mypy/type-check command is configured, and none is claimed. New modules and
Phase 5 tests were formatted with Ruff. The temporary plan dialog was also rendered
and visually inspected offscreen for readable stages, routing, limits and controls.

## End-to-end fixture measurement

Executed and asserted the JSON outcome from:

```text
uv run --frozen python -m monster.mission.strategic.pilot --directory /tmp/monster-phase5-final-pilot --scenario replan_restart --approve-simulation
```

Observed: completed / six verified objectives / no remaining objectives; two
strategic calls, one reviewed revision, seven stage starts, nine charged model
calls, zero duplicate effect dispatches and zero native input transactions. The
first two completed outcomes survived revision and restart without replay. Schema
validity, admission and successful-replan rates were 1.0 **for this deterministic
fixture only**. Token costs and live-model latency/reliability were not measured.

The real Ollama adapter was exercised with MockTransport for plan and revision
schemas, response completion, token/context limits, text-only transport, routing
consent and errors. Those tests are not evidence of live model quality. The
unavailable-endpoint test verifies no endpoint fallback. Live two-model
benchmarking remains PENDING; no configured endpoint was contacted.

## Migration and limits

Schema 5 adds bounded strategic records to the existing ledger, retaining legacy
mission/effect/approval/schedule/checkpoint state. Tests exercise schema 4 migration,
backup preservation, unknown future schema refusal, admission/revision rollback,
and ledger commit before graph checkpoint. Stop Monster and retain a consistent
backup of the entire state directory before upgrading; restore the backup to roll
back, rather than decrementing a schema number.

This is a conservative Phase 5 simulation catalog, not a general-purpose task
interpreter. Intake recognizes the documented design-proposal family and asks for
clarification on unsupported constraints. Revision editing is restricted to one
catalog strategy substitution at an evidenced pre-dispatch boundary, preserving
its identity and rubric; arbitrary additions or graph editing are not exposed.
Raw goals, documents, screenshots, secrets and model reasoning are not stored in
strategic tables. A real design document is not created by fixture success.

The strategic layer cannot repair an operational vision model's uncertainty,
wrong focus or wrong-action grounding. Native dynamic planning remains disabled.
See the Windows qualification document for the required independent trials and
`PHASE5_STRATEGIC_INTELLIGENCE.md` for the targeted, unimplemented Phase 6
workspace-isolation proposal.
