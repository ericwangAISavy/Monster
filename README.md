# Monster 🧠

**Universal AI Computer-Using Agent**

Continuing development in another workspace? Read the
[workspace checkpoint](docs/WORKSPACE_CHECKPOINT.md) for the current branch,
verified implementation state, setup commands and next V4 step.

Monster is an AI-powered desktop agent that can visually understand and autonomously operate Windows applications using screenshots, mouse movements, and keyboard interactions.

Its mission is simple:

**Let AI use your computer the way you do.**

## Core Features

- **Visual Computer Understanding** — Understand desktop interfaces through screenshots.
- **Autonomous Computer Control** — Perform mouse and keyboard operations.
- **Local AI Support** — Run models locally or across LAN through Ollama.
- **Multi-Model Architecture** — Support Gemini, OpenAI, Anthropic, OpenRouter, Groq, and custom model endpoints.
- **Bring Your Own API Key** — Choose and configure your preferred AI provider.
- **Autonomous Task Execution** — Complete multi-step tasks using natural-language instructions.
- **Visual Verification** — Observe the results of actions and recover from errors.
- **Privacy and Control** — Support local inference, approval gates, and user-controlled execution.

## Agent Architecture

Observe → Reason → Act → Verify

## Initial Technology Direction

- **Platform:** Windows
- **Primary AI Provider:** LAN-hosted Ollama
- **AI Models:** Vision-language models
- **Computer Interaction:** Screenshot, Mouse, Keyboard
- **Architecture:** Modular, provider-agnostic agent engine

## Development Status

🚧 Planning and early development.

## Vision

Monster aims to become a universal computer-use agent capable of operating applications through their graphical interfaces without requiring application-specific integrations.

## Cursor companion GUI

The first runnable milestone is an ultra-minimal PySide6 companion: a tiny
click-through character and an on-demand command pill. **It currently uses a
mock controller and performs no AI inference or computer actions.**

From the repository root on Windows with Python 3.12+ and uv:

```powershell
uv sync --frozen
uv run monster
```

Press **Ctrl+Alt+M** or use the tray icon to activate. **Ctrl+Alt+P** pauses/resumes
and **Ctrl+Alt+X** stops the simulation. Tray Settings contains configurable
shortcuts, cursor offsets and reduced motion. Tray Quit exits the application.

See [Cursor companion guide](docs/CURSOR_COMPANION.md) for architecture,
tests, capture isolation, engine integration and required Windows validation.
See [Architecture plan](docs/ARCHITECTURE_PLAN.md) for the broader product design.

## Real Windows computer tools

A separate, explicit manual harness now provides screenshot capture and real mouse
and keyboard operations. The companion remains a mock; it does not invoke these
input tools automatically.

```powershell
uv sync --frozen
uv run monster-tools displays
uv run monster-tools screenshot capture.png --display 1
```

Quit the companion before input tests so the worker can exclusively register
**Ctrl+Alt+X**. Open a disposable Notepad document, then run the following and
focus the editor during the three-second countdown:

```powershell
uv run monster-tools type "Hello from Monster"
```

For isolated overlay capture testing: `uv run monster --capture-test isolated.png`.
See [Computer tools guide](docs/COMPUTER_TOOLS.md) for mouse commands, physical
coordinates, stop behavior, limitations and required Windows qualification.

## LAN Ollama preview

Tray → **AI model settings…** now supports native Ollama connection testing,
installed-model discovery, a synthetic vision/JSON probe and an opt-in screenshot
preview mode. After enabling preview, submit an instruction through the pill and
inspect **Last action preview…** from the tray.

**Preview sends one desktop screenshot to your configured local/LAN server; it
proposes an action but never executes it.** The default remains the GUI demo.
See [LAN Ollama guide](docs/OLLAMA.md) for server setup, privacy, tests and limits.

## Supervised real input (experimental)

AI model settings now also offers **Supervised real input · approve each action**.
Start with an already-open disposable Notepad document. Monster asks for approval
of each action, rechecks the screen, executes through the independent-stop worker,
and observes the result. Completion requires your visible-result confirmation.
**Ctrl+Alt+X** stops; no unattended or sensitive workflows are supported.

See [Supervised execution](docs/SUPERVISED_EXECUTION.md) for instructions, limits
and outstanding native Windows qualification.

In **AI model settings → Real computer control**, toggle **Ask human approval
before every action**. It defaults on. Turning it off automates routine
text/navigation/movement and model-classified benign clicks/Enter. Sensitive,
uncertain or unclassified actions and final completion still require review. Screen rechecks and Ctrl+Alt+X remain
active. See the [approval setting](docs/SUPERVISED_EXECUTION.md#choose-whether-every-step-needs-approval).

For typing into an already-open chat composer, include the exact text and
**“Do not send.”** The loop now checks the visible result after input, remembers
actual recent actions, and blocks repeated failed clicks or duplicate text. Limit
stops are reported as **Stopped incomplete**, with details in **Last failure**.
See the [Slack draft workflow](docs/SUPERVISED_EXECUTION.md#typing-an-unsent-message-in-an-existing-slack-composer).

## Diagnose a failed run

Tray → **Last run report…** contains the original instruction, model/settings,
runtime/source identity, ordered proposals and exact rejection reasons, review
responses, observations, mapped input coordinates, receipts, verification evidence
and timings. **Last failure…** also includes the content-bearing report automatically.
You do not need to reconstruct or separately quote the instruction.

Use **Save report…** for JSON, or **Save with screenshots…** for a ZIP containing
the report and recent retained PNG observations. The ZIP is the useful artifact
for diagnosing wrong-target clicks and visual-verification failures. Reports can
contain task/action text; screenshots can contain personal desktop content.
Review locally before sharing. Nothing is saved or uploaded automatically, and
raw model reasoning and credential/environment dumps are not collected. Evidence
retention limits and any omitted frames/events are stated in the report.

The companion is a continuously animated pastel pocket monster with a soft pearl
body, big blinking eyes, rosy cheeks and expressive paws. Vector animation stays
sharp at every supported size without a GIF player or extra dependencies. Idle sleeps with closed eyes and slow breathing;
thinking tilts its head, looks upward and touches its temple; working leans forward
with a springy bounce and alternating paws; completion celebrates with a wave.
Active poses animate at about 30 fps even when parked in a corner. Ready, waiting
and stopped have their own gentle movements. There are no visible status labels, text panels or
spinning circles. Screen-reader state descriptions remain available. The existing
command/approval pill retains the information required to review actions.

In **Settings → Character**, choose Pearl monster, Cocoa monkey, Rose rabbit,
Peach tiger, Lavender cat, Cloud pigeon or Matcha ox. Thumbnails and a small live
preview show the chosen character before saving. All seven use the same state
language with species-specific ears, tails and details; no assets are downloaded.
Old preference files retain the original monster.

In **Settings → Companion size / position**, choose 75–150% size and cursor
following (default) or a primary-display corner. Reduced motion freezes animation while
preserving each pose. Model settings scroll independently of their request controls, and
connection/vision tests use the thinking pose until they end or cancel.

Real screenshot capture still briefly hides both Monster overlays to keep them out
of the PNG. A successful Windows capture-exclusion hint alone is not treated as
proof of isolation. Synthetic **Test vision + JSON** sends generated color panels,
never a desktop screenshot. Repeated passive approval prompts now keep the final
window flags instead of switching through an intermediate focus mode.

The adopted [v2 architecture](docs/ARCHITECTURE_V2.md) is being implemented
incrementally; see [migration status](docs/V2_MIGRATION.md). The first new increment
is an isolated [UI-Venus native-format preview adapter](docs/UI_VENUS.md), with an
offline synthetic fixture command and no real-input integration or automatic model
switch. Native Windows and exact model/runtime qualification remain required;
[benchmark records](docs/RELIABILITY_BENCHMARKS.md) distinguish tests from evidence
of real task reliability.

For reproducible v2 evidence collection, see the [qualification workflow](docs/QUALIFICATION.md):
native preflight and acceptance records, read-only grounding trials, metadata-only
metrics and an advisory readiness inventory. These tools do not enable UI-Venus
input or change the selected model.

Above Windows Start/Search requires the optional [signed UIAccess visual renderer](docs/WINDOWS_UIACCESS.md),
configured explicitly with `--overlay-helper`. The agent/input worker remain ordinary
processes. The signed Windows build and above-menu behavior still require native qualification.

## Durable mission orchestration (v3 Phase 1)

An additive, **fake-only** LangGraph mission layer now supports SQLite checkpoints,
bounded subtasks, approval interruptions and crash recovery without ambiguous
action replay. It is not wired to the companion or real-input controller.
See [v3 architecture and offline demos](docs/LONG_HORIZON_ARCHITECTURE_V3.md)
and [implementation evidence](docs/MISSION_PHASE1_IMPLEMENTATION.md).

## Experimental mission / GUI bridge (v3 Phase 2)

On Windows, the tray now offers an explicit two-subtask Notepad mission and resume
entry while using the existing supervised controller. Each subtask, action and
final result still requires review. The adapter runs mission coordination off the
Qt thread, holds one desktop writer, waits for cleanup, and never replays ambiguous
input. Default modes remain unchanged. **Native Windows qualification is pending.**
See [integration and limits](docs/PHASE2_GUI_INTEGRATION.md) and
[the opt-in Windows smoke procedure](docs/WINDOWS_PHASE2_QUALIFICATION.md).

## Effect journal and recovery (v3 Phase 3)

Experimental missions now journal each action, bind review to one exact effect,
consume approval transactionally before input, and block uncertain continuation.
Unknown clicks and external/destructive/credential/financial actions remain blocked.
The existing pill provides exact-action review and an observe-only human
reconciliation entry. Standalone supervised defaults are unchanged.
See [effect lifecycle, policy and privacy](docs/MISSION_EFFECTS_AND_POLICIES.md) and
[tests and pending Windows qualification](docs/PHASE3_QUALIFICATION.md).

## Bounded six-stage mission pilot (v3 Phase 4)

The tray offers a default **six-stage simulation**, observe-only rehearsal,
requested progress, safe-boundary pause and explicit resume. A durable scheduling
envelope binds consent, budgets and evidence above the existing graph. Unknown
work is not replayed and costs are not reset on restart.

```powershell
uv run python -m monster.mission.pilot --directory .monster-missions --scenario success --approve-simulation
```

This runs fake subtasks only. The new native pilot is disabled without explicit
reviewed Phase2/3 qualification metadata; **native qualification remains PENDING**.
See [execution architecture and commands](docs/PHASE4_LONG_HORIZON_EXECUTION.md)
and [qualification/evidence](docs/WINDOWS_PHASE4_QUALIFICATION.md).

## Strategic planning simulation (v3 Phase 5)

The experimental Phase 5 layer proposes a typed objective DAG, validates it
against a fixed simulation catalog, binds plan/revision consent to exact digests,
and evaluates the original goal rubric against journal evidence. It reuses the
Phase 4 scheduler and retains all effect/stop boundaries. **Dynamic native desktop
planning is disabled; Windows qualification and live-model benchmarks are PENDING.**

Tray → **Phase 5 · plan and simulate…** accepts the supported design-proposal
fixture goal. Review the temporary plan dialog, then consent to the simulation.
The **safe replan simulation…** pauses after two synthetic outcomes; choose
**review safe revision…** to inspect the alternate strategy. **Resume simulation…**
requires fresh consent. This creates no real document and sends no screenshots.

```powershell
uv run python -m monster.mission.strategic.pilot --directory .monster-missions --scenario replan_restart --approve-simulation
```

See [strategic architecture](docs/PHASE5_STRATEGIC_INTELLIGENCE.md),
[model routing and context](docs/PHASE5_MODEL_ROUTING_AND_CONTEXT.md), and
[qualification status](docs/WINDOWS_PHASE5_QUALIFICATION.md). Planning simulations
are not evidence that arbitrary desktop tasks or uncertain visual checks work.

## Independent workspace simulation (v3 Phase 6)

Tray → **Workspaces…** adds an explicit Personal Desktop / Independent Workspace
simulation selector. Register one guest, verify the simulated pairing, start the
reviewed six-stage fixture and approve its guest-issued challenge in the pill.
Guest status, stop, reconnect/resume and one consented synthetic preview are
available without routing guest commands to the host input controller.

```powershell
uv run python -m monster.guest --diagnose
uv run python -m monster.workspaces.simulation --scenario six_stage_fake
uv run python -m monster.workspaces.simulation --scenario partition_pause
uv run python -m monster.workspaces.simulation --scenario crash_reconcile
```

**This is a portable simulation: no VM, TLS sockets, real screenshot or native
input.** Live TLS/Windows credential and guest-input integration remains gated by
the Phase 6 prompt's design review and pending Windows qualification. Personal
Desktop stays the default. The guest role retains the existing engine and journal;
the host projection never becomes a second authoritative mission database.

See [workspace architecture / ADR006](docs/PHASE6_WORKSPACE_ARCHITECTURE.md),
[manual VM guide](docs/PHASE6_VM_SETUP.md), [security review](docs/PHASE6_SECURITY_MODEL.md),
[operations and recovery](docs/PHASE6_OPERATIONS_AND_RECOVERY.md), and
[native qualification](docs/WINDOWS_PHASE6_QUALIFICATION.md).
