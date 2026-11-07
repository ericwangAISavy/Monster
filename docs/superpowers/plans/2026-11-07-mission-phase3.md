# Phase 3 — effect journal and deterministic dispatch policy

**Spec:** supplied CODEX_MONSTER_V3_PHASE3_PROMPT.md; v3 architecture.
**Baseline:** Phase 1/2 present at6229be6;586 passed,3 native skipped in101.67s.

## Design and constraints

Extend existing Effect/Approval contracts with version2 action bindings; preserve
v1 subtask records. Add schema3 action projections and immutable event history in
the same repository. Keep SQLite operations on the mission worker; Qt requests
prepare/approve/consume through queued signals. Commit one-time approval consumption
and dispatch intent before the existing controller can queue execute. Input receipt
is not outcome evidence. Stop/corrupt storage fail closed.

Only fixed scratch text and a narrow keyboard/movement set can be reviewed live.
Unclassified clicks and external/destructive/credential/financial operations remain
blocked. Model/page text cannot grant authority. No new input/capture engine.
HMAC content fingerprints use a volatile RAM key; restart revokes old grants and
cannot recover the key. No sensitive content or key is written to checkpoints.
Reconciliation is a human observe-only handoff using the existing pill, never retry.

## Tasks

1. RED→GREEN policy matrix, versioned contracts, schema3 migration, append-only journal,
   fingerprint/approval scope, atomic consumption, receipt/outcome and restart rules.
2. RED→GREEN Qt/controller policy hook before worker dispatch; async journal requests,
   cancellation/fresh-scene checks, exact review labels and fail-closed storage errors.
3. Human reconciliation/audit metadata via existing compact UI; block unresolved mission
   continuation; retention protects unresolved/audit records.
4. Full tests/lint/smoke, independent review, docs covering P3-01..30 and native gaps.

## Review focus

Approval consumption races; journal errors before input; late Qt callbacks; ambiguous
receipt vs outcome; restarted grants; human attestation provenance; privacy of digests.

## Completion ledger

- Tasks1–3 implemented: schema3 and version2 action bindings; async journal gate; exact pill controls; observe-only human attestation; unresolved/audit retention.
- Review findings fixed with RED→GREEN regressions: provider callback content bypass; uncertain in-attempt continuation; global reconciliation queue and false empty-success on storage errors.
- Task4: fullsuite631 passed,3 native skipped in94.36s; frozen sync, Ruff, formatting, compileall and offscreen startup passed. Mypy not installed/configured; attempted command reported unavailable.
- Ruling: no real external/persistent workflow enabled; simulated consequence approvals exercise protocol only. Unknown navigation clicks fail closed rather than using model risk labels.
- Ruling: volatile HMAC key avoids a new secret vault/storage flow; old grants cannot be reused across restart. Human references replace cross-restart raw content reconstruction.
- Ruling: observe-only reconciliation uses human inspection, not a second capture engine. Attestation does not auto-complete the whole subtask or retry input.
- Native Windows/input/model qualification remains pending, including Phase2 gates.
