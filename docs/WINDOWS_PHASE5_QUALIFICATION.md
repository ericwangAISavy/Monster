# Windows Phase 5 qualification — PENDING

**No native Phase 5 execution, live-model benchmark or Windows qualification has
been performed.** Earlier Phase 2/3/4 Windows qualification is also PENDING. All
new strategic execution is simulation-only, including on Windows and even if an
operator supplies a Phase 4 qualification object. No Phase 5 native-enable switch
exists. Portable CI uses fake runners and offscreen Qt only.

## Portable evidence

The baseline was 667 passed, 3 skipped. Added tests are in
`tests/mission/test_phase5_strategic.py` and `test_phase5_ui.py` and run in the
existing CI suite without new dependencies or external services. The final exact
commands/results are recorded in the Phase 5 validation report.

| Required scenarios | Coverage |
| --- | --- |
| P5-01, 48–49 | Full prior suite, unchanged standalone worker/controller/approval policy |
| P5-02–13 | Goal intake, strict JSON/schema/IDs/fields, DAG shape/cycles/dependencies, catalog/scope, criterion coverage/gaps, visual predicate refusal |
| P5-14–18 | Exact consent/epoch/digest, concurrent revision/late response, history, rename and evidence binding guards, uncertain dispatch after restart |
| P5-19–21 | Preflight-only replacement, one attempt per blocker, call/replan/global budgets, no circular strategy |
| P5-22–27 | Ledger-based completion, receipt/model claims refused, human provenance, negative evidence, hallucinated semantic evidence |
| P5-28–30, 50 | Extra-field prompt-injection fixture, command/coordinate/API refusal, credential handoff, raw error/content absent from SQLite |
| P5-31–35 | Mocked real Ollama text transport, unavailable/incomplete/truncated responses, capability/config/route consent, shared/separate role descriptors, bounded context |
| P5-36–42 | Goal immutability/new intake, restart/private context, transactional admission/revision rollback, ledger before graph recovery, no duplicate replay, additive schema4 migration, SQLite backup, future schema refusal |
| P5-43–47 | Six-stage fake pilot with two outcomes/blocker/review/restart, unsupported requirement remains partial, stop/late provider callback, Qt review cancellation, prior focus/preflight regressions |

Fault injection exercises admission transaction failure, revision transaction
failure, evaluation failure after a committed outcome, crash before the first
checkpoint, uncertain dispatch, restart before revised work, stop during planning,
and pause during a racing revision. Generated DAG fixtures exercise canonical
hashing and valid dependency combinations without adding a property-test package.
Tests do not create accounts, post, send, pay, deploy or use live desktop input.

## Required native work before any future pilot

1. Complete and review existing Phase 2, 3 and 4 Windows qualification first.
2. Record source hash, OS/build, Python/Qt, DPI/monitors, hardware/GPU, model ID and
   digest, inference runtime and data destination for every actual trial.
3. Verify ordinary standalone and two-stage supervised paths remain unchanged.
4. Qualify a future bridge restricted to the existing reviewed local-editor
   catalog; dynamic labels must not supply arbitrary typing, clicks or APIs.
5. Review exact plan scope/digest, model/data routing, budgets and revision effects.
6. Check fresh scene/focus and Phase 3 intent/exact-effect approval at every input.
7. Pause/resume after verified progress; confirm no duplicate text or effect.
8. Inject safe pre-dispatch blockage and ambiguous post-dispatch loss separately.
   The latter must reconcile, never replan around uncertainty.
9. Test independent Ctrl+Alt+X, manual takeover, late callbacks and held-input
   cleanup during planning, review, execution and verification.
10. Change window, monitor and DPI between observations. Confirm conservative pause.
11. Distinguish machine visual evidence, human attestation, simulation and missing
    proof in every report. Inspect storage/logs for content leakage.
12. Measure planner quality and operational reliability independently, using at
    least two explicitly consented model configurations where available.

Do not mark these items PASS from fixture tests. No production-account workflows
or external effects belong in this qualification.
