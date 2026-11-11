# Whole-branch reliability review and repairs

User requests an entire review and fixes. Baseline: work/950b6a6, clean tree.
Read-only independent reviews cover (1) mission/effects/workspace control,
(2) native input/capture/controller handoff and (3) providers/settings/GUI.
Parent reviews Worker contracts and benchmark/native qualification boundaries.
Native Windows and actual model behavior cannot be qualified from this review.

Confirmed findings to reproduce before fixing:
- Settings compatibility comparison retains hands_free while probe config omits
  it; enabled hands-free cannot save despite a successful probe.
- Double-click checks target arrival once; takeover after first release sends the
  second click to a different position. Stop partial/unknown, no steering/replay.
- Settings also caches error-free negative/wrong-model probe objects as a success;
  validate through the existing positive typed compatibility contract before caching.
- Host caches stop confirmation across a newly accepted mission/review, masking
  current running/review state and later session requalification messages.

- [x] Add failing regressions for all three paths, including preserved negative
  controls (inference settings invalidate probe; stable double-click still works;
  refused new work does not erase a genuine stop acknowledgment).
- [x] Repair root causes with existing boundaries and no permission expansion.
- [x] Run focused and full serial frozen tests, lint, compilation, startup and
  diff checks. Document reviewed scope/evidence/limits and any deferred findings.
- [x] Update checkpoint, commit and push normally to work; main untouched.

Evidence: fail-first UI/input: 4 failed / 2 negative controls passed. Actual guest
stop leaves mission paused; corrected fixture to resume that mission rather than
starting a conflicting new mission. Old-source stop/resume + invalid-session
projection regression: 2 failed. UI negative/wrong-model acceptance: 3 failed;
None/True coverage added. Repairs made without limits/permissions changes.
First affected-module run: 167 passed, 3 failed (two existing transport/Qt timing
failures plus fixture using start instead of resume). Next: 174 passed, one
existing multi-action timing failure; that test passed unchanged in isolation.
Logs preserved under /tmp/whole-review-*. Final confirmation pending.
Independent repair review found no important issues. Previous forged result flag,
metadata and authority coverage gap is now covered through controller refusals.

Affected module confirmation: 175 passed in 38.37s; forged-envelope suite: 27 passed in 0.52s. Final repair review: no important findings. Full serial suite running; no timeout or permission changes.

Final verification: 1353 passed, 3 skipped in 360.38s. Setup/lint/compile/startup/diff checks passed; independent repair review found no important issues. No native/model qualification claimed. Normal commit/push to work only.
