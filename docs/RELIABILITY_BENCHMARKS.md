# Reliability evidence and qualification gates

Targets in [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md) are release gates, not achieved
measurements. Record model checkpoint/digest, quantization/projector, server/version,
GPU/VRAM, client Python/Qt/Windows build, monitor layout/DPI, task set and trial count.

| Area | Current evidence | Qualification |
| --- | --- | --- |
| Existing controller/input | Portable fake-provider/input tests; supplied Windows reports exposed real targeting failures | Not a completed Windows acceptance suite |
| UI-Venus parser/coordinates | Generated PNG fixtures, pinned native action format, 0–999/provenance/invalid-action/terminal tests | Protocol correctness only; not model accuracy |
| UI-Venus transport | Mocked OpenAI-compatible request/response, stale/cancel/concurrency/privacy tests | Exact live checkpoint/runtime/projector untested |
| UI-Venus grounding / Q5,Q6,Q8 | No held-out model trials in this environment | Not measured |
| Windows input/capture/stop | Linux execution environment; no attached native Windows runner | Pending native qualification |
| Hands-off completion / 95% gate | No qualified representative suite | Not achieved or advertised |

## Native stage 0 checklist

Use disposable content and the existing COMPUTER_TOOLS, OLLAMA and
SUPERVISED_EXECUTION instructions. Record each check as pass/fail/unavailable with
runtime identity and evidence; do not infer a pass from a portable simulation.

- Unicode text insertion exactly once in a disposable editor.
- Physical coordinates, monitor gaps, negative origins and 100/125/150/200% DPI.
- Foreground restoration after command/approval pill, including keyboard approval.
- Overlay-free actual PNG pixels, focus/lock changes and geometry changes.
- Emergency stop under load, held-input cleanup and parent/worker failure.
- Partial/unknown insertion: stop, inspect, and verify no automatic replay.
- Cold/warm model requests, timeout/cancellation, late replies and refusal states.
- Draft workflow already focused, then visible/unfocused, both approval settings;
  correct field, exact text, no Send or submission, honest incomplete outcomes.

## Stage 1 / 2 results record

For each selected checkpoint/runtime/quantization, fill in:

| Field | Value |
| --- | --- |
| Checkpoint / SHA / quantization / projector | Not supplied or downloaded |
| Server runtime and version | Unqualified for this adapter |
| GPU / VRAM / offload / context | Not measured |
| Windows build / Python / Qt | Native test run required |
| Display/DPI matrix | Native/held-out fixtures required |
| Model-generated correct target hits / attempts | Not measured |
| Verified drafts/tasks / attempts | Not measured |
| Wrong-target / duplicate / false completion | Not measured |
| Capture, transfer, queue, inference, validation, execution, settle, verification p50/p95 | Not measured |
| Stop p50/p95/p99 and recovery outcomes | Not measured |

Stop on any unintended consequential action. Completion claims remain hypotheses
until independent visual and human verification supports the original criteria.

## Reproducible collection tools

See [QUALIFICATION.md](QUALIFICATION.md) for executable native preflight, operator
acceptance records, read-only grounding benchmarks and advisory readiness checks.
The bundled three-case recorded replay exercises the harness only. It is not
live model accuracy, tool reliability, latency or a release qualification result.
