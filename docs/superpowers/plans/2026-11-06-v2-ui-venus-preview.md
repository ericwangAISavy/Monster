# V2 UI-Venus preview implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. Steps use checkbox syntax.

**Goal:** Implement the v2 exact next task as an isolated preview-only native UI-Venus adapter, with no real-input integration.
**Architecture:** Preserve existing modules. Parse one bounded native action using an explicit AST allowlist, normalize to immutable versioned preview contracts with frame/geometry provenance, and offer offline replay and explicit private-LAN OpenAI-compatible preview requests.
**Tech stack:** Existing Python/PySide6/httpx/uv; no additional runtime dependency for this first isolated increment.
**Spec:** docs/ARCHITECTURE_V2.md, sections 0–9 and 11–13.

## Global constraints

- Screenshot/Mouse/Keyboard only for target apps; adapter has no executor or capture dependency.
- No policy changes, automatic model switching, credentials, unattended input, model downloads, raw reasoning persistence or default screenshot storage.
- Keep 0–999 UI-Venus mapping separate from existing generic 0–1000 mapping.
- Finished is an unverified completion claim; CallUser needs human input; invalid/partial/ambiguous output never becomes success.
- Stage 0 native Windows and exact model serving remain explicitly unqualified.

## Review focus

- Hostile AST expressions and deeply nested/oversized output must not execute or leak content in errors.
- Crop/negative-origin coordinates and single-pixel endpoints preserve source geometry.
- Missing pointer anchors, held inputs, repeated hotkeys, multiline Type and sequences must not silently become supported input.
- Stale replies after frame/generation/model/task changes and concurrent requests cannot enter accepted history.
- Offline CLI/default reports contain metadata only, never thought blocks, screenshots or typed payloads.

### Task 1: Native action parsing and versioned preview contracts

Files: providers/ui_venus.py, providers/venus_actions.py, tests/test_ui_venus.py.
- [x] Write failing fixture tests for supported actions, malformed/malicious syntax, bounds and terminal signals.
- [x] Implement bounded AST/literal parsing, immutable native-call representation and action-specific validation.
- [x] Implement normalized image/desktop coordinates and provenance; no guessed conversions or input execution.
- [x] Verify tests pass. Keep native unsupported semantics explicit and separate from current tools.

### Task 2: Frame-bound preview inference

Files: providers/ui_venus.py, tests/test_ui_venus.py.
- [x] Test explicit LAN configuration, native prompt, image messages, completion flags, response bounds, no retries and stale/concurrent response rejection.
- [x] Implement opt-in OpenAI-compatible transport and accepted-action-only bounded history. Strip thought content; no persisted screenshots/raw model output.
- [x] Verify all tests, preserving existing native Ollama and live controller behavior.

### Task 3: Offline replay and qualification documentation

Files: providers/ui_venus_preview.py, tests/fixtures/gui_synthetic/, tests/test_ui_venus.py, docs/UI_VENUS.md, docs/RELIABILITY_BENCHMARKS.md, README.md.
- [x] Generate non-private screenshot fixtures and test CLI parse/replay with no networking/input and safe defaults.
- [x] Document upstream pin, supported action subset, 0–999 formula, serving protocol and untested checkpoint/quantization/projector constraints.
- [x] Record Windows qualification blocker and migration gates; no fabricated benchmark success.
- [x] Full tests, lint, startup, independent review, commit and non-force push.

## Validation evidence

Full portable suite: 396 passed before two additional transport-limit tests;
final adapter suite: 89 tests including those additions. Synthetic replay: 7/7.
Ruff and diff whitespace checks passed; offscreen startup exited successfully.
Independent read-only review found no blocking issues; deadline/size tests and
context-invalidation documentation address its follow-ups. Native Windows and
live UI-Venus inference were not tested.
