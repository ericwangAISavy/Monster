# Experimental supervised Windows execution

Monster can now take a model's proposed action through the real input worker, **one action at a time, with per-step approval enabled by default**. The existing GUI demo and one-screenshot preview remain available. This milestone connects the components; it does not establish general GUI reliability or qualify native Windows behavior.

## First test: a disposable Notepad document

This walkthrough uses the default per-step approval setting. Automatic routine
actions are described in the approval-setting section below.

1. Update and launch from the repository:

   ```powershell
   git pull
   uv sync --frozen
   uv run monster
   ```

2. Open tray → **AI model settings…**. Keep your existing LAN URL and model; run **Test vision + JSON** for the current settings. Select **Real computer control (experimental)** and Save.
3. Open Notepad manually with an empty, disposable document on display 1. Close sensitive applications and other Monster instances. Keep the target standard privilege; no administrator elevation is needed or supported.
4. Activate with **Ctrl+Alt+M** and request a small task such as **“Type Hello from Monster into the open Notepad document.”** Start with a short sentence, not a complex cross-application workflow.
5. Monster checks the model, captures display 1 with its own overlays hidden, and shows an approval in the existing pill. Review the operation, coordinates/key/text payload and visible target. Scroll long text to inspect it. No input is sent just because a proposal appeared.
6. Choose **Approve** for that specific action or **Decline** to stop. Escape also cancels. After approval the pill hides, Monster restores the previously observed foreground window, captures a fresh frame and checks whether the scene still matches. A changed scene invalidates the approval and triggers a fresh proposal instead of executing stale input.
7. After a successful input insertion, Monster captures again. The next model call receives the current screenshot and a bounded history of input receipt categories, and decides whether to continue or propose completion. Every additional input needs another approval.
8. When the model reports completion, inspect Notepad yourself. The final pill asks you to confirm the visible result against your original instruction. Only that explicit confirmation produces **“Completed · visible result confirmed by you.”** A model claim or an input receipt alone is not recorded as completion.

This mode can move your real pointer and type into real applications. Keep tests non-sensitive and do not interact with the desktop during an approved transaction. Decline uncertain targets. A passing synthetic model probe does not demonstrate GUI grounding accuracy.

## Stop and manual takeover

- **Ctrl+Alt+X** is owned by the spawned input worker during a supervised task, including inference and approval waits. The Qt process relinquishes its same shortcut registration first; the worker must register it successfully before any input can be armed. Another program owning the shortcut causes startup failure.
- The worker independently invalidates input generations and releases its owned keys/buttons on emergency stop. Worker interruption events cancel the controller as well.
- Tray **Stop task** invalidates queued bridge work and requests worker stop immediately. After cleanup is acknowledged, the GUI registers its idle stop shortcut again.
- **Ctrl+Alt+P**, activation during an active supervised task, and opening secondary settings are manual takeover: they **stop the supervised run**. They do not retain/replay a pending approval. Start a fresh task afterward. Demo/preview pause semantics are unchanged.
- The real worker always uses Ctrl+Alt+X. A custom GUI stop shortcut may also work through Qt, but is not an independent worker hotkey. Activate/pause bindings cannot use Ctrl+Alt+X in supervised mode.
- If cleanup cannot be confirmed, the UI reports it and blocks new supervised tasks until you inspect held input and restart Monster. Quit requests disarming/cleanup before tearing down the bridge; forcibly killed processes or blocked OS calls cannot guarantee cleanup.

## Runtime boundaries

A supervised run is limited to **10 input transactions, 20 decision calls and five minutes**. Each approval/clarification expires after 60 seconds. Three proposals of the same action on the same captured pixels stop as no visual progress. More than two changed-scene revalidations also stop. These are proposed conservative operating limits, not model-performance guarantees.

The worker is disarmed after each successful transaction and remains unarmed during inference and user review. It has its own heartbeat/30-second arm lease and two-second held-input limits. Stop/failure invalidates pending bridge epochs, task generations and approvals. Partial/unknown insertion results stop rather than replaying text/clicks. There is one serial input client and one input executor.

Keyboard operations are tied to a captured foreground HWND and checked in the input worker. Native HWNDs are used only for focus infrastructure, not reading application titles, text, accessibility trees or state. Key presses that intentionally change focus (such as Start/Enter) release their owned key without treating that expected transition as unrelated focus theft. Text input retains the foreground check for every event. Mouse actions may themselves change foreground; they are scene-checked before execution, then observed again afterward.

Action coordinates are initially relative to the captured native image. The controller validates that coordinate scope, adds the captured display origin, validates against real physical monitor rectangles, and includes the expected geometry in the worker command. Mixed-DPI mapping still needs real Windows qualification. Changing geometry disarms input.

Scene revalidation compares display geometry, foreground identity, the exact native-pixel patch around mouse targets, and a downsampled whole-image change threshold. It tolerates small unrelated animation such as a caret, but is **not a security guarantee**. Small changes or races between capture and input can escape detection. Repeated revalidation failures require stabilizing the desktop, not disabling checks. Pixel comparison runs off the UI thread.

The initial policy blocks Delete, unapproved chord types, and common command/executable/URL text patterns. Allowed chords are limited to selected editing/navigation combinations. These filters are deliberately conservative and **cannot prove an arbitrary target is safe**. Enter or a mouse click could still have consequences in the wrong application. There is no semantic application allowlist or automatic physical-user activity detector yet. For this milestone, use a disposable Notepad/test desktop and inspect every proposal. Email sending, purchases, installation, credentials, administrative/destructive workflows and unattended operation are outside the supported scope.

Long approval payloads are rendered as plain selectable text inside a bounded scroll area within the same pill. Oversized requests beyond 3,500 characters cannot be approved. There is no “approve all,” generated-code execution, shell tool, app API or DOM automation. Requests still use the configured LAN Ollama adapter; no cloud fallback exists.

## Data handling and implementation

Screenshots, task text, pending decisions and the last action preview remain in process memory. No transcripts/screenshots are persisted by this mode. Approval necessarily displays the proposed text; avoid secrets. Native worker receipts omit typed payloads. The bounded model history contains transaction categories rather than typed contents. The server still receives screenshots and the original instruction under your selected local/LAN configuration.

- `agent/controller.py`: lifecycle, model/capture/approval/receipt loop, budgets and user-confirmed completion.
- `agent/validation.py`: image-coordinate mapping, conservative policy and scene comparison.
- `agent/input_bridge.py`: serial off-UI client operations, cancellation epochs, independent worker interruption signals and cleanup.
- `computer/worker.py`, `client.py`, `executor.py`: optional observation geometry/focus bindings, explicit disarm acknowledgment and owned-input enforcement.
- `app.py`: hotkey ownership handoff, capture/focus infrastructure, unchanged minimal companion surfaces and controller switching.

## Validation status

Automated tests use fake model/capture/input primitives. They verify no input before approval, rejection/expiry/stale-answer behavior, fresh capture comparison, negative-origin mapping, foreground/geometry rejection, bounded bridge cancellation, explicit disarm/rearm protocol, cleanup failure blocking, shortcut handoff, and final human confirmation. Existing GUI, provider and worker tests remain in the suite.

**Native Windows behavior has not been verified in the Linux development environment.** Before treating this as qualified:

1. Run the existing [computer-tool checklist](COMPUTER_TOOLS.md) and inspect overlay-free captured pixels.
2. Test a harmless one-sentence Notepad run; inspect every proposed coordinate and text, every receipt and final visible result.
3. Confirm the approval pill loses focus and input goes to the original Notepad window. Deliberately change the window/layout while approval is open; verify that no stale action executes.
4. Try Ctrl+Alt+X during model latency, approval, typing and immediately after approval. Verify no later queued input and no held modifiers/buttons. Repeat shortcut ownership transfer across tasks and model modes.
5. Test declining, Escape, 60-second approval expiry, task deadline, no-progress loops, server loss, worker loss and cleanup-failure reporting on a dedicated test desktop.
6. Check long payload scrolling, multiple DPI layouts, negative monitor origins and focus-changing keys. Record actual results/timings; do not infer native reliability from fake-backend tests.

The next stage is improving measured grounding/verification and recovery from these trials. Removing supervision or expanding to sensitive applications requires a separate design and evidence, not merely a working happy-path demo.

## Reporting a failure after approval

If the task fails after Approve, open tray → **Last failure…**. The report remains
available after the transient pill disappears and can be selected/copied. It
identifies the failed stage (model check, decision or screen recheck), unexpected
exception class/source location, and confirmed input transaction count. Exception
messages, traceback locals and provider payloads are not included in unexpected-
error diagnostics. A zero transaction count does not prove no partial input
occurred; inspect the desktop before retrying.

Copy that report with your OS and model identifier when asking for help. The old
“Provider operation failed unexpectedly; nothing was executed” message could also
be emitted by local screen revalidation and was not sufficient to identify the
cause. A stage-specific report is diagnostic evidence, not proof that an
underlying Windows-only failure has already been fixed.

## Choose whether every step needs approval

In tray → **AI model settings…**, choose **Real computer control (experimental)**
(the existing `supervised` mode), then use **Ask human approval before every
action**:

- **Checked (default):** every input transaction needs an explicit approval.
- **Unchecked:** routine typing, movement and navigation may proceed automatically.
  Ordinary clicks and Enter/Space/Start also proceed automatically when the model
  explicitly classifies their visible target/effect as `routine`. Actions classified
  `sensitive` or `uncertain`, unclassified clicks, and locally detected sensitive
  intent still require review. Every retained prompt states its reason. Unsupported
  shortcuts and existing blocked actions remain blocked.

Final visible-result confirmation and clarification prompts remain enabled in
both cases. Existing policy-blocked actions remain blocked. This setting controls
per-step review, not tool permissions, data destinations or the emergency stop.
The checkbox is disabled in demo/preview modes, which do not execute real input.

The choice is saved in `ollama.json`; old settings default to approval enabled.
Changing it applies to the next run and does not require another vision probe
when reopening already-saved preview/control settings with unchanged inference
values. Runtime model checks still occur at task start. Opening settings stops any
active real-input run rather than changing its policy in flight.

Automatic steps take the same fresh screenshot, scene/focus/geometry checks,
worker dispatch, post-action observation and task limits as approved steps. They
**do not restore an old foreground window** after human focus changes. No fake
approval event is generated. Ctrl+Alt+X always remains the independent worker
stop. Routine-operation classification cannot guarantee semantic safety in every
application; this experimental mode remains intended for a disposable test
workspace, not unattended sensitive workflows.

### Routine-action classification

Native Ollama action decisions now include `risk: routine | sensitive | uncertain`.
The prompt distinguishes benign editor/navigation clicks and harmless Enter
operations from sending, deletion, payments, installation, credentials or other
consequential actions. Local sensitive-intent detection can escalate a `routine`
proposal to review. Legacy responses without risk metadata remain compatible,
but their clicks/submit keys are treated as unclassified and ask for review.

This model assessment is fallible and is not proof that a GUI action is safe. The
experimental mode is intended for non-sensitive tests; scene validation, operation
limits, blocked-action policy and independent stop remain active. Opening settings
or changing policy never silently authorizes an already-pending proposal.
