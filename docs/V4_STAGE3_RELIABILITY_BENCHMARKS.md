# V4 stage 3: grounding and verification benchmarks

The five-step sequence is: (1) durable authorization, (2) bounded Worker
extraction, (3) grounding/verification measurements, (4) native Windows
qualification, and (5) separately gated broader capabilities. Steps 1–2 have
portable implementation coverage. Stage 3 now has read-only measurement tooling;
real-model measurements remain pending. Stages 4–5 remain gated.

## Run the benchmark

From the repository, replay the application-owned synthetic fixtures without a
server, desktop capture or input:

```bash
uv run --frozen python -m monster.benchmarks.reliability --manifest tests/fixtures/reliability/cases.json --repeats 3
```

To measure the currently selected Ollama model, explicitly supply its existing
saved `ollama.json` and consent to sending the fixture instructions and images:

```bash
uv run --frozen python -m monster.benchmarks.reliability --manifest tests/fixtures/reliability/cases.json --config PATH_TO_EXISTING_OLLAMA_JSON --allow-fixture-data --repeats 3 --deadline 600
```

No configuration is written, model substituted, image captured or input dispatched.
An installed wheel needs an explicit manifest path. The saved server must already
have local-server confirmation. Run comparisons sequentially with the same dataset
hash and runtime revision. A model alias is recorded; its checkpoint digest remains
unknown rather than being inferred. Read-only measurements never grant execution
permission or replace existing qualification records.

## Cases and independent scoring

The 18 cases cover composer focus, exact unsent text, duplicate text, wrong field,
Send exclusion, search results versus launched applications, canvas endpoints,
visible drawing results, obscuration and completion. Seventeen generated PNGs
include 100/125/150/200 percent render scales and a negative display origin. These
are synthetic pixel coordinates and annotations, not physical monitor tests.
Grounding instructions specify the exact visible target or required method:
click the Message placeholder, drag marker A to marker B, or press Enter on the
highlighted result. Oracles evaluate that instruction rather than an unstated
preferred action. Both drag endpoints must match.

The strict versioned manifest binds each bounded PNG by SHA-256, rejects duplicate
IDs, unknown fields and path escapes, fully decodes images, and checks before/after
bounds. Images are revalidated before each trial. Independent region/text-hash and
status labels are withheld from provider prompts. Held-out manifests can declare
human review; this declaration is not independently attested by the runner.

The existing BoundedWorker calls the selected provider once per trial. It has no
capture, input, ledger or authorization port. There are no automatic retries;
explicit repeats are separate benchmark trials. Trial count and total duration
are bounded, cancellation propagates, and three consecutive inference failures
stop the run. Fixture/tool errors stop immediately. Error reports use fixed
categories and never include private exception text or raw model answers.

## Interpret results

`recorded_replay`, `injected_provider`, and `live_read_only` are separate modes.
Only the real configured provider reports model accuracy and model latency.
Replay deliberately includes faulty answers to test detection. `status=completed`
means all trials finished, not that all predictions were correct; inspect
`all_predictions_matched`, outcomes and coverage. CLI exit status follows completion.

Verification reports a three-status confusion matrix. A success prediction against
failed **or uncertain** ground truth counts as false success. The false-positive
denominator is assessed failed/uncertain cases; the false-negative denominator is
assessed succeeded cases and includes abstentions. False failure separately counts
explicit failed predictions against success. Unassessed errors are reported with
coverage, not silently counted as correct. Missing denominators yield null rates.

Pipeline latency includes image reading, validation and scoring. The separate
inference-and-validation interval includes provider waiting and Worker contract
validation; neither is GPU execution time. Operation percentiles are client
measurements. Replay times are withheld. Wrong-target, repeated-text and unsupported
completion counters describe proposals, not executed actions. Executor errors,
duplicate dispatches, successful drafts, recovery outcomes and native stop latency
remain null because this runner never dispatches input.

Reports contain metadata, counts, IDs, hashes and timings. They do not contain
screenshots, task contents, raw answers, verification evidence or model reasoning.
Nothing is persisted automatically; stdout can be redirected deliberately. Do not
add personal screenshots to the checked-in synthetic dataset.

## Qualification still required

Replay validates the scorer and failure accounting, not model skill. Synthetic
images do not establish Slack, Windows focus, physical DPI, smooth mouse movement,
overlay exclusion or emergency-stop reliability. The private Ollama access probe
in this workspace returned HTTP 403; endpoint availability was not established.
This is not evidence of a model defect. No live model comparison was performed.

Next, run this benchmark on the selected model and a reviewed held-out dataset,
inspect wrong predictions locally, then perform [native qualification](QUALIFICATION.md).
For the unsent Slack draft, measure focused/unfocused composer × per-step approval
on/off, exact insertion once, wrong targets, duplicates, verification latency,
interventions and Ctrl+Alt+X cleanup. Never submit the message. Keep failed or
ambiguous effects unresolved until explicit reconciliation. Stage 4 must be done
on Windows; portable tests cannot qualify it.

## Portable validation evidence

The recorded replay completed all 54 planned trials (18 cases × 3), with 21
matching the independent oracle. Deliberate faults produced six wrong-target
proposals, three repeated-text proposals, three unsupported completion proposals,
15 false-success verifications and three false failures. All 36 verification
trials were assessed; their false-positive rate was 0.625 and false-negative rate
0.25. These numbers describe the supplied faulty recordings, not a real model.
Model accuracy and latency were null. No native input transaction occurred.

Twenty-four new regression cases cover scoring, uncertainty, privacy, consent,
strict manifests, hashing/tampering, cancellation/deadlines, failure attribution,
falsey injected providers, duplicate text and visible target/oracle alignment.
Independent review found ambiguous grounding instructions in three fixtures;
visible markers and exact target/method instructions now resolve that issue.

Final Linux offscreen regression confirmation: **1,253 passed, 3 skipped** in
307.29 seconds. Frozen setup, lint, compilation, formatting and startup smoke
passed. An earlier run had three existing timing failures; all affected modules
passed unchanged on rerun and the complete confirmation then passed. The
[checkpoint](WORKSPACE_CHECKPOINT.md) preserves that evidence and limitations.

Stage 4 now has a [per-attempt native evidence assessor](V4_STAGE4_NATIVE_TRIALS.md)
for the four approval/focus cases. Tooling availability does not complete native
qualification; actual Windows and selected-model evidence remains pending.
