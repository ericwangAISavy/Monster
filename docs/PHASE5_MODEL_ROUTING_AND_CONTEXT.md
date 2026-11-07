# Phase 5 model routing and context

Phase 5 has static strategic and computer-use roles. The same provider/model may
fill both descriptors, but data consent is explicit for each role. Descriptor
capabilities are requirements, not proof of reliable live-model behavior.
Structured responses must still validate on every call. There is no automatic
model download, endpoint fallback, dynamic routing auction or cloud tracing.

## Default

The tray and pilot default to a deterministic fake strategic planner and the
existing fake GUI runner. No model request, screenshot transmission, native input,
real document or generated design content is involved. The fixture's natural
language intake maps only its supported design-proposal family; an unrelated goal
requires clarification.

## Optional saved Ollama planner

The optional `OllamaStrategicPlanner` reuses `OllamaProvider.request`, with the
existing URL restrictions, TLS verification, proxy settings, redirect refusal,
local-server confirmation, bounded HTTP reads and redacted transport failures.
It uses a separate text schema and does not change the GUI model contract. No
image or tool-call field is sent. Incomplete, truncated, tool-bearing, oversized,
deeply nested, duplicate-key, non-finite and extra-field responses fail closed.

An explicit local configuration file may be selected for the pilot:

```text
uv run python -m monster.mission.strategic.pilot --directory .monster-missions --scenario success --approve-simulation --planner-config PATH_TO_EXISTING_OLLAMA_SETTINGS --allow-planner-data
```

Inspect that saved endpoint before consenting. The flag authorizes sending the
reviewed reference-only rubric, application-owned catalog and minimal evidence
metadata to that endpoint. Raw task text is handled locally and not transmitted.
`ModelRoleConfig` binds a routing alias, model ref, capabilities, prompt version
and configuration digest; it stores no raw URL or key. The adapter verifies its
actual RAM-only configuration against that digest and model before transport.
Goal destinations must independently include the planner destination; role consent
alone cannot enlarge the goal's routing scope. GUI execution stays synthetic even
when the optional planner uses Ollama. Live GUI/model integration is disabled.

There is no configured/reachable Ollama instance in the cloud validation
environment. MockTransport tests exercise the real adapter contract, completion
flags, data routing, token controls and failures; they are not live inference
benchmarks. Using two production model configurations remains **PENDING**.

## Resource controls

Default/hard limits: six/eight planner calls; two/three replan attempts; one
attempt per blocker; thirty/sixty seconds per call; 2,048/4,096 output tokens;
16,384 context tokens (default and hard maximum); 32/64 KiB response bytes; 16/32 KiB context bytes;
JSON nesting sixteen. Schema/system overhead is conservatively included in the
context estimate; requests that could truncate constraints are refused. Provider
settings may reduce these caps but never enlarge them. A provider adapter permits
one concurrent request, and a mission rejects a second in-flight planning call.
No transaction spans inference. Provider token usage is recorded only when
reported; fake-model usage remains null instead of becoming a fabricated cost.

Every request is reserved before awaiting the model. Cancellation, timeout,
invalid output and storage failure do not refund mission call counts. Replans
also consume the existing scheduler's global model-call envelope. A late response
cannot commit after stop, pause, epoch change, parent revision change or expiry.

## Three context levels and retention

- Mission: reviewed safe goal/criteria refs, immutable active plan digest,
  model/route identity, evidenced outcome refs, call/replan counts and budgets.
- Objective: catalog objective and dependencies, recent verified outcomes and one
  pre-dispatch blocker. No raw application content is accepted as authority.
- GUI action: existing fresh capture, scene/focus and geometry bindings, authorized
  next action, recent actual action feedback. Phase 5 never sees these images.

References and bounded enums are persisted, rather than accumulating free-form
model summaries. Criterion facts are rebuilt from the authoritative ledger on
each context request; changing evidence invalidates the next reconstructed view.
Existing effect evidence IDs preserve provenance. Goal/plan changes do not
rewrite old facts. Private context is RAM-only; a required missing private context
pauses after restart. No encryption/key store, vector database, credential intake
or document persistence was added. Intake detects explicit credential/MFA/CAPTCHA
requests and requests human handoff without invoking a model. Unknown extra
constraints require clarification rather than being silently dropped.

Planner call rows hold operation, completion status, validation category, latency,
reported input/output tokens and prompt version only. The exception's raw text,
model response, user instruction, screenshot, chain of thought and secrets are
not written to these tables. Evaluations retain at most sixteen snapshots per
mission; other histories are bounded by call/revision budgets and pruned with
terminal missions. Never enable global cloud tracing to diagnose a mission.

## Measurements and report interpretation

The deterministic pilot uses six synthetic GUI outcomes, one blocked preflight,
two strategic calls (plan plus revision), seven stage starts, one revision, nine
global model calls charged and zero native input transactions. Restart preserves
the first two verified outcomes. These counts are fixture results, not estimates
of real Windows/model performance. Wall/latency values are measured per run and
vary; there is no claimed production latency or cost target.

Reports expose criterion coverage, strict proposal validity/admission rates,
planner call latency/tokens, revision count, verified outcomes, duplicate effect
dispatches, human interventions and scheduler budgets. Unsupported-detection and
semantic-accuracy benchmark rates remain null where no labeled benchmark
denominator exists. Live-model benchmarking is explicitly PENDING. A successful
synthetic goal says nothing about whether the operational vision model can focus
or type reliably in Notepad.
