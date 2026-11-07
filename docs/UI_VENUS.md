# UI-Venus-2 native computer previews

This is **v2 roadmap stage 1 software foundation**, not a qualified replacement
for the current model or a new input executor. Current Ollama settings, companion,
controller policies and emergency-stop code are unchanged. There is no GUI model
switch, weight download, automatic fallback, real input or automatic success.

## Upstream format evidence

Reference: [UI-Venus-2 computer example](https://github.com/inclusionAI/UI-Venus/blob/6d5f8baf3d28868b3e32fefa9da501e99964a8c5/models/computer/computer_example.py).
Inspected revision: `6d5f8baf3d28868b3e32fefa9da501e99964a8c5`.
Source SHA-256: `844cb2efb504c7febca83cede6dd28a55c46c5701cd32c627d01e300df6bf812`.
Computer README SHA-256: `88a002a7fd1f35efef321ebc9552519e31e7472d519a54db2a21762a93edd6b3`.

The upstream example is inference-only, uses an OpenAI-compatible multimodal
service and emits native calls, optionally following a `<think>` block:

```text
<action>Click(box=(500, 500))</action>
<action>Type(content='Hello from Monster')</action>
<action>Finished(content='Outcome to verify')</action>
```

Monster implements the meaning of that format with its own bounded AST/literal
allowlist. It never imports/runs the upstream example, evaluates model expressions,
or copies the upstream password-bearing system prompt. Upstream default parse
retries, held-input actions and open-loop sequences are deliberately not adopted.
Model/checkpoint licenses still need checking before distribution; no weights are
bundled or downloaded here.

## Preview contracts

`DecisionRequest` snapshots task ID, generation, objective, PNG and immutable
`FrameBinding`: observation ID, hash, physical bounds, display topology, timestamp
and opaque foreground guard. PNG dimensions/hash are validated independently. Objectives are bounded to
1–4000 total characters (including surrounding whitespace); blank objectives and
unpaired Unicode surrogates are rejected before any request is sent.
`PreviewDecision` is immutable and versioned (`schema_version=1`); action mappings
are read-only. It carries image-relative and desktop-physical actions separately,
plus coordinate provenance. Every result has `execution_allowed=false` and
`verification=unverified`. It is not an approval token or input command.

The initial isolated contracts use frozen dataclasses/manual validation to
preserve the current interfaces/runtime. V2's Pydantic discriminated live-action
contracts are deferred to supervised integration (stage 3), not introduced by
rewriting the current controller in this first increment.

### Coordinates

UI-Venus uses **0–999**, not the existing generic Ollama adapter's 0–1000 format.
The pinned upstream conversion is:

```text
image_x = min(width  - 1, int(x_999 * width  / 999))
image_y = min(height - 1, int(y_999 * height / 999))
desktop_x = image_x + supplied_physical_origin_x
desktop_y = image_y + supplied_physical_origin_y
```

Both endpoints, fractional coordinates, 1×1 images, crop origins and negative
monitor origins are supported. Points in known monitor gaps are rejected. There
is no Qt DIP scaling or implicit resize. The caller supplies real geometry; a PNG
alone cannot establish its desktop origin/topology. Offline file timestamps are
load times, not claims of a fresh live desktop. Preview binding rejects replaced
contexts; input freshness/expiry authorization remains a live-controller concern.

### Initial action subset

| Native action | Preview result |
| --- | --- |
| Click / DoubleClick / RightClick / MiddleClick with `box` | Explicit image and desktop coordinates, button and bounded count |
| Hover with `box` | Move candidate |
| Drag with both start and end | Bounded 0.5s drag candidate |
| Type with bounded non-control Unicode content | One text candidate |
| Hotkey with known named key(s), repeat 1 | Single press/chord candidate, still requiring live policy authorization |
| Wait | Observation suggestion; no automatic capture/input |
| CallUser | Needs-user signal, no execution |
| Finished | Unverified completion claim, never verified success |

Missing pointer anchors, TripleClick, Swipe (unqualified scroll units/current
cursor), held keys/buttons, repeated hotkeys and Sequence are explicitly rejected.
Newlines/control characters in Type are rejected rather than silently converted to
Enter, stripped, split or replayed. This is narrower than upstream and intentional.
A syntactically valid preview is still not a safety/risk approval.

## Run offline without a model

```powershell
uv sync --frozen
uv run python -m monster.providers.ui_venus_preview --fixture tests/fixtures/gui_synthetic/venus_cases.json
```

The seven cases use generated non-private PNGs and predefined candidate responses.
`matched=true` measures parser/mapping expectations, **not model grounding or task
completion**. No network calls, desktop capture, input or result files occur.

Parse a previously saved response using explicit geometry:

```powershell
uv run python -m monster.providers.ui_venus_preview --response response.txt --screenshot screenshot.png --origin-x 0 --origin-y 0
```

Single-image mode treats the supplied rectangle as one display/crop; use the
fixture manifest's `displays` rectangles or the Python API when supplying virtual
desktop topology/gaps. Origins are required and never guessed. Reports redact text
payloads/terminal messages by default. `--include-content` explicitly reveals
those fields, never `<think>` blocks or separate reasoning fields. Inputs are read
only; nothing is saved automatically.

## Opt-in serving compatibility

`UIVenusComputerAdapter` sends `POST /v1/chat/completions` to an explicitly supplied
server **origin**, using `image_url` PNG data URIs, native text output and no forced
generic JSON/action schema. It requires exactly one assistant choice, matching
served model identity and `finish_reason=stop`; missing/length/tool/error endings
are rejected. This is stricter than upstream's acceptance of missing finish flags.
Requests have total deadlines and bounded responses, no redirects or retries.
Malformed or excessively nested JSON is reported as a safe provider failure; the
offline CLI also stops cleanly on excessively nested fixture JSON. Neither path
accepts a candidate or prints the response body when parsing fails.

Only localhost/private IP origins are supported initially. Plain LAN HTTP needs
explicit confirmation; screenshot transmission needs local-only confirmation.
TLS verification remains enabled. Authentication/BYOK, private DNS names/custom API
paths and cloud providers are not implemented in this stage. The server operator's
local-only assertion is not remote attestation of model routing.

After separately installing and qualifying the actual checkpoint/projector/runtime,
a single explicit synthetic preview can be requested, for example on a locally
served OpenAI-compatible endpoint:

```powershell
uv run python -m monster.providers.ui_venus_preview --server http://127.0.0.1:8000 --model UI-Venus-2-9B --task "Click the purple test button" --screenshot tests/fixtures/gui_synthetic/desktop_1080.png --origin-x 0 --origin-y 0 --local-server-confirmed
```

This command is an example, not evidence that a server/model is installed. For a
private LAN IP also pass `--allow-lan-http` when using HTTP. The active Monster
Ollama model is not read, changed or reused implicitly. Ollama's `/v1` image path
may be usable only if the **exact** UI-Venus checkpoint, conversion, vision
projector and chat template work together; a model listing or generic vision/JSON
probe does not prove that. vLLM/llama.cpp support likewise needs actual testing.
Q5/Q6/Q8 and Q6_K remain candidates, not measured winners.

## State, history and limits

Only one request is in flight per adapter instance. Callers must invoke
`set_current_request()` or `invalidate()` immediately when context changes during
inference; a second `next_decision()` is rejected as busy and does not replace
the current context. Invalidation, new task,
generation, frame/geometry/foreground, objective or model changes make late replies
stale. Accepted history contains only validated canonical native actions and at
most eight turns, two images/24 MiB; defaults are six turns and no historical
images. Thought text and raw response bodies are never retained. Historical
candidates are labeled **not executed**; they are not progress evidence. Separate
manual processes are not globally serialized.

Full Windows qualification, held-out model grounding, stop-latency measurements,
actual checkpoint/quantization/GPU results and connection to real input are still
pending. See [V2_MIGRATION.md](V2_MIGRATION.md) and
[RELIABILITY_BENCHMARKS.md](RELIABILITY_BENCHMARKS.md).
