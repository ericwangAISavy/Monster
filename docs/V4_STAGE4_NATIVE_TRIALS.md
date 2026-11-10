# V4 stage 4: per-attempt native draft evidence

This slice implements the read-only evidence workflow for stage 4. It does **not**
complete Windows qualification. The current cloud executor is Linux; native input,
physical display/focus, stop cleanup and actual Slack behavior remain unmeasured.
Stage 5 remains gated. The existing controller, executor, durable authorization,
mission scheduler and permission checks are unchanged.

## Why this exists

The existing [native qualification record](QUALIFICATION.md) collects aggregate
checks. The new assessor ties each repeated draft attempt to one existing run
report and to the operator's visible observations. A model's completion claim or
an `inserted` receipt cannot supply those observations. Failed, partial and unknown
attempts remain visible and block advisory review eligibility. The tool never
captures a screenshot, sends a model request, starts a task, dispatches input,
updates settings or writes evidence files automatically.

## Prepare on the actual Windows desktop

Fully quit Monster, update the existing `work` checkout using normal Git
operations while preserving local changes, and sync dependencies. Restart Monster
from that same updated checkout before collecting run reports. A report from an
older process/source cannot inherit the current qualification record.

```powershell
uv sync --frozen
uv run --frozen python -m monster.qualification preflight
uv run --frozen python -m monster.qualification template > native-record.json
uv run --frozen python -m monster.benchmarks.native_trials template --repeats 3 > draft-trials.json
```

Templates leave every measurement unknown. Do not replace Linux runtime metadata
with Windows strings or treat synthetic fixture records as native evidence. Use
the actual Windows-generated record. The default sample is three attempts per
case, twelve total; this is an initial engineering sample, not a statistical
reliability guarantee. `--repeats` accepts 1–20. Do not lower the sample size to
hide a failed attempt or edit failures into passes.

First perform the independent Unicode, physical-DPI/display, focus restoration,
overlay exclusion, stop and held-input cleanup checks in [QUALIFICATION.md](QUALIFICATION.md).
Do not run the standalone input harness while Monster owns its worker/hotkey.
Keep the selected model unchanged. Run the [stage 3 benchmark](V4_STAGE3_RELIABILITY_BENCHMARKS.md)
against that model when reachable; replay is not a substitute.

## Perform and record each draft attempt

The four cases are already named in the template:

| Case | Composer before task | Per-step approval |
| --- | --- | --- |
| `draft_focused_approval` | Empty and focused | On |
| `draft_focused_automatic` | Empty and focused | Off |
| `draft_unfocused_approval` | Empty, visible, unfocused | On |
| `draft_unfocused_automatic` | Empty, visible, unfocused | Off |

Use real computer control mode, with hands-free mode off for this particular
approval matrix. Use this exact instruction so a report cannot silently refer to
a different task:

> Type 'Hello from Monster' into the currently open Slack message composer. Do not send.

Begin each trial fresh. Target operations must use Screenshot, Mouse and Keyboard;
no Slack API, DOM, accessibility, clipboard or command-execution shortcut. Never
click Send or submit a message. Use keyboard approval where possible to avoid
moving the target pointer. A normal requested review/final confirmation is not a
recovery intervention. Stop on wrong targets or ambiguous/partial input; inspect
and explicitly reconcile before starting another task. Do not replay it.

After input cleanup finishes, use tray → Last run report → Save report (JSON,
without screenshots). Keep the original file locally beside `draft-trials.json`.
Existing full reports contain task/action text and may contain other private
content: **do not commit or automatically share them**. This assessor reads them
locally but outputs only fixed metadata projections. ZIP/screenshot bundles are
not consumed. Add each report reference to its corresponding trial:

```powershell
(Get-FileHash .\run-01.json -Algorithm SHA256).Hash.ToLower()
```

```json
"report": {"path": "run-01.json", "sha256": "THE_64_CHARACTER_LOWERCASE_HASH"}
```

Populate `observed` using what you actually saw:

- `started_empty`, `initially_focused`, `intended_field`, `exact_text`, `unsent`:
  explicit booleans; leave null if you could not tell.
- `insertions`: number of visible insertions; `duplicate_typing`: whether any text
  was repeated, including partial repeated text; `wrong_target_actions`: count.
- `interventions`: recovery/takeover actions beyond ordinary requested approval;
  `recovery`: `not_needed`, `recovered`, `failed` or `unknown`.
- `failure_stage`: `none`, `unknown`, `capture`, `grounding`, `proposal_validation`,
  `focus`, `input_dispatch`, `verification`, `completion` or `cleanup`.
- `failure_domain`: `none`, `unknown`, `model`, `provider`, `tool`, `executor` or
  `host`. These are **operator-reported classifications**, not automatic diagnoses
  from a receipt or model answer. Use unknown when the evidence is insufficient.
- `stop_requested`: explicit true/false; if true, record `stop_confirmed` and an
  independently measured `stop_latency_ms`. `held_input_clear` records whether
  owned held input was visibly/independently cleared. Unknown cleanup blocks review.

The assessor never infers exact text or draft success from model verification.
Any failed/unknown observation, wrong initial focus, unintended send, duplicate,
intervention or recovery blocks a clean draft. Every referenced run must be
terminal and idle, with confirmed cleanup, no cleanup failure and no pending
model/input/review job. A requested but unconfirmed stop also blocks review;
measured stops must meet the hardware-specific threshold in the native record.
Keep separate stop/cleanup tests in the native protocol; do not manufacture a
successful draft from a cancelled stop test.

## Assess locally

```powershell
uv run --frozen python -m monster.benchmarks.native_trials assess --manifest draft-trials.json --native native-record.json
```

The assessor combines the existing native checklist with the four-case matrix.
Initially it will report missing checks/observations: this is expected. Use the
per-case attempt/supported/incident/intervention/recovery counts to reconcile the
corresponding aggregate native-record draft entries. Do not erase a failed trial
just to align counts. Unknown incident counts remain null. Independent native
checks, false-completion counts and stop samples still belong in the original
native record. They are not inferred from these draft records.

Report files are size-bounded, hash checked, and confined to the manifest directory.
Duplicate trial IDs, reused report hashes or run IDs cannot count as new trials.
The exact protocol task, policy setting, exported full configuration, current
source fingerprint, native runtime and worker source provenance must agree.
Changed models/settings cannot be pooled into one review-eligible sample. Source
changes require new matching evidence; a model alias/configuration hash does not
attest the server's actual weights, hardware or projector. No permissions depend
on the output.

Output contains successful drafts/attempts, per-case counts, all failed/unknown
trial blockers, configuration digests, operation waits, operator failure domains
and requested-stop outcomes. Planning/verification percentiles are **per-trial
sums of client waits**, not per-call GPU/model latency. Missing waits stay null;
cancelled waits remain included in exported totals. `real_input=false` describes
the assessor itself, not the referenced runs. `review_eligible=true` means only
internally consistent declared evidence, not authenticity, Windows qualification,
a model qualification or an execution grant. Those flags remain false.

Exit code is 0 for a template or advisory review-eligible inventory, 1 for blocked
or malformed evidence. Invalid files produce a fixed error without private paths,
raw report contents or tracebacks. Nothing is persisted unless the operator
explicitly redirects stdout. Keep resulting metadata private if its identifiers
are sensitive.

## Portable implementation validation

The new assessor has 59 synthetic regression cases; combined native-checklist,
diagnostics and stage 3 regressions passed 115 tests. Independent review found
incomplete-configuration and contradictory cleanup/phase acceptance gaps, both
reproduced by failing tests and repaired. The final full Linux offscreen suite
passed **1,312 tests, 3 skipped in 265.45 seconds** after cloud recovery. Frozen
setup, lint, compilation, formatting, startup and diff checks passed. Direct CLI
smoke correctly blocked an unmeasured 12-slot template with zero attempted or
successful trials and no authorization/qualification. The checkpoint retains the
earlier interrupted run's two workspace failures and their unchanged 91-test
module rerun. No native/real-model behavior is established by these results.
