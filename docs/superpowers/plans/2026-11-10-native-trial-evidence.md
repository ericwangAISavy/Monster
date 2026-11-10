# Native trial evidence implementation plan

> Execute inline with executing-plans, then obtain one independent source review.

**Goal:** Make stage 4 repeated native draft evidence inspectable per attempt.
**Architecture:** Strict bounded local manifest plus hashed existing run exports;
reuse `qualification.assess_native`; no execution or permissions integration.
**Tech stack:** Python 3.12, existing Pydantic/strict JSON/percentile utilities.
**Spec:** ../specs/2026-11-10-native-trial-evidence-design.md

## Constraints and review focus

No new executor/ledger, network, desktop capture, settings writes or automatic
replay. Reject invented native success, copied runs, changed hashes/source/model,
policy/task mismatches, missing observations, failure/uncertainty erasure and
private-content leaks. Null timing/observation data remains unknown, never zero.

## Task 1: read-only native evidence assessor

- [x] Write `tests/test_native_trials.py`: template unknown; consistent synthetic
  four-case evidence; wrong-target/duplicate/sent/partial/stop/cleanup failures;
  failed run cannot pass despite operator success; missing/repeated runs and matrix;
  source/config/task/policy/worker binding; strict JSON/path/hash/schema/privacy;
  null latency and explicit operator failure attribution; CLI no writes/input.
- [x] Run red tests before creating implementation.
- [x] Implement `src/monster/benchmarks/native_trials.py`: `template(repeats=3)`,
  `assess_trials(manifest_path, native_record)` and `main(argv=None)` with template
  and assess commands. No reports written except explicit stdout redirection.
- [x] Run focused tests, qualification/diagnostics/benchmark regressions, full
  frozen serial suite; lint, compile, startup and diff checks. Review and repair.
- [x] Document actual Windows procedure and privacy; update checkpoint and
  stage status without claiming qualification. Commit/push work normally.

Evidence ledger: baseline 23b1af1. First RED: 38 missing-module failures; GREEN:
38 passed. Edge/review RED: 13 failures for unknown zero counts, oversized CLI,
missing configuration and contradictory cleanup/phase; GREEN combined suite:
115 passed, including 59 new cases. Independent reviewer found the two reproduced
metadata fail-open issues; both repaired. No deferred findings. Full serial
verification pending at this ledger entry.

Decision: exact diagnostic configuration fields are required, validated using
existing OllamaConfig without filling missing defaults. Current exports include
phase/cleanup_failed; older/incomplete exports remain blocked rather than inferred.

Recovery ledger: cloud exec transport disconnected at 99%. Staged changes survived.
Retained full run: 1310 passed, 3 skipped, 2 existing workspace test failures.
Both affected complete modules rerun unchanged after recovery: 91 passed in
20.32s. Fresh final full-suite confirmation is running; no production/test limits
changed. Frozen setup, lint, compile, formatting, diff checks and startup passed.

Final verification: 1312 passed, 3 skipped in 265.45s; all prior failures passed.
Direct CLI smoke: unmeasured template blocked (12 planned, 0 attempted), no
execution or qualification. All setup/lint/format/compile/startup/diff checks
passed. User-authorized delivery is a normal commit/push to work only. No core
execution source, permissions, dependency locks or production limits changed.
