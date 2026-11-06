# Run diagnostics implementation plan

> Implement inline in this session using test-first validation.

**Goal:** A self-contained failure report that identifies the original task and the
actual failed stage without reconstructing the run through questions.

**Architecture:** One bounded in-memory recorder per run, controller instrumentation,
and explicit export through the existing details UI. No automatic disk logging or
raw reasoning. Optional screenshot ZIP export contains recent native PNGs and their
observation/step links; omitted/evicted evidence is reported explicitly.

**Tech stack:** Python 3.12, standard-library JSON/ZIP/hash/platform metadata, PySide6.

## Constraints and review focus

- Preserve input/review/stop policies and no-replay behavior.
- Include task/model/budgets/runtime fingerprint, ordered proposals, policy reasons,
  approval responses, geometry, mapped coordinates, receipts and paired verification.
- Preserve redacted `failure_diagnostics`; content-bearing local reports are labelled.
- Never collect credentials/environment dumps, raw reasoning, target APIs or clipboard.
- Bound events and image memory; disclose all truncation/eviction.
- Capture terminal stage before cleanup and distinguish cleanup failure from primary failure.
- Bind exports to a frozen run snapshot; disclose pending cleanup and never mix later work.
- Cover no-action/startup failure, policy rejection, uncertain/partial input, cancellation,
  negative-origin mapping, stale callbacks, screenshot retention and explicit-only persistence.

## Tasks

- [x] Write recorder tests: task/version metadata, ordered immutable snapshots,
  redaction, bounded images/events, JSON/ZIP explicit export without overwrite.
- [x] Implement `agent/diagnostics.py` with run IDs, runtime metadata, events,
  observation IDs, report rendering and export.
- [x] Write controller integration tests for rejected proposals, receipts,
  verification links, instruction preservation and cleanup/terminal outcomes.
- [x] Instrument controller boundaries and UI approval/focus handoffs.
- [x] Add Last run report plus text/screenshot export and frozen report identity.
- [x] Validate privacy, all tests, lint and startup; document usage and limits.
- [x] Commit and non-force push to main after final review.

## Review outcome

An independent read-only review identified cross-run attribution of late background
exceptions/worker events. A regression reproduced it; bounded job-origin metadata
now excludes stale payloads and preserves origin context for current events. The
reviewer approved that fix. Native Windows execution remains outside Linux proof.
