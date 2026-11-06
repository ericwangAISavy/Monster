# Monster 🧠

**Universal AI Computer-Using Agent**

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
text/navigation/movement steps; clicks, submit keys, uncertain/sensitive operations
and final completion still require review. Screen rechecks and Ctrl+Alt+X remain
active. See the [approval setting](docs/SUPERVISED_EXECUTION.md#choose-whether-every-step-needs-approval).
