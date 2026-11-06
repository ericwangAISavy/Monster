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
3. Open Notepad manually with an empty, disposable document on your intended display with that window foreground. Close sensitive applications and other Monster instances. Keep the target standard privilege; no administrator elevation is needed or supported.
4. Activate with **Ctrl+Alt+M** and request a small task such as **“Type Hello from Monster into the open Notepad document.”** Start with a short sentence, not a complex cross-application workflow.
5. Monster restores the pre-pill target, checks the model, captures that foreground window's display with its own overlays hidden, and shows an approval in the existing pill. Review the operation, coordinates/key/text payload and visible target. Scroll long text to inspect it. No input is sent just because a proposal appeared.
6. Choose **Approve** for that specific action or **Decline** to stop. Escape also cancels. After approval the pill hides, Monster restores the previously observed foreground window, captures a fresh frame and checks whether the scene still matches. A changed scene invalidates the approval and triggers a fresh proposal instead of executing stale input.
7. After input insertion, Monster captures again and asks the model to verify the concrete expected result in the intended field. The next planning call receives the current screenshot and bounded actual-action/outcome history. Failed actions are not blindly repeated. Additional input follows your configured approval policy.
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

A supervised run is limited to **10 input transactions, 20 decision calls and five minutes**. Each approval/clarification expires after 60 seconds. Visually failed actions and nearby coordinate-jitter retries are blocked regardless of screenshot hashes. Three repeated/invalid proposals stop incomplete; limits never count as completion. One visual verification call is allowed per inserted transaction, separately from the 20 planning calls. More than two changed-scene revalidations also stop. These are proposed conservative operating limits, not model-performance guarantees.

The worker is disarmed after each successful transaction and remains unarmed during inference and user review. It has its own heartbeat/30-second arm lease and two-second held-input limits. Stop/failure invalidates pending bridge epochs, task generations and approvals. Partial/unknown insertion results stop rather than replaying text/clicks. There is one serial input client and one input executor.

Keyboard operations are tied to a captured foreground HWND and checked in the input worker. Native HWNDs are used only for focus infrastructure, not reading application titles, text, accessibility trees or state. Key presses that intentionally change focus (such as Start/Enter) release their owned key without treating that expected transition as unrelated focus theft. Text input retains the foreground check for every event. Mouse actions may themselves change foreground; they are scene-checked before execution, then observed again afterward.

Action coordinates are initially relative to the captured native image. The controller validates that coordinate scope, adds the captured display origin, validates against real physical monitor rectangles, and includes the expected geometry in the worker command. Mixed-DPI mapping still needs real Windows qualification. Changing geometry disarms input.

Scene revalidation compares display geometry, foreground identity, the exact native-pixel patch around mouse targets, and a downsampled whole-image change threshold. It tolerates small unrelated animation such as a caret, but is **not a security guarantee**. Small changes or races between capture and input can escape detection. Repeated revalidation failures require stabilizing the desktop, not disabling checks. Pixel comparison runs off the UI thread.

The initial policy blocks Delete, unapproved chord types, and common command/executable/URL text patterns. Allowed chords are limited to selected editing/navigation combinations. These filters are deliberately conservative and **cannot prove an arbitrary target is safe**. Enter or a mouse click could still have consequences in the wrong application. There is no semantic application allowlist or automatic physical-user activity detector yet. For this milestone, use a disposable Notepad/test desktop and inspect every proposal. Email sending, purchases, installation, credentials, administrative/destructive workflows and unattended operation are outside the supported scope.

Long approval payloads are rendered as plain selectable text inside a bounded scroll area within the same pill. Oversized requests beyond 3,500 characters cannot be approved. There is no “approve all,” generated-code execution, shell tool, app API or DOM automation. Requests still use the configured LAN Ollama adapter; no cloud fallback exists.

## Data handling and implementation

Screenshots, task text, pending decisions and the last action preview remain in process memory. No transcripts/screenshots are persisted by this mode. Approval necessarily displays the proposed text; avoid secrets. Native worker receipts omit typed payloads. The bounded in-memory model history now includes recent actual action payloads (including typed text), expected results and visual verification feedback so the planner can avoid duplicating input. This history is sent only to the same configured inference server and is not logged or persisted. Human-shareable failure traces redact typed text and verification evidence. The server still receives screenshots and the original instruction under your selected local/LAN configuration.

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

## Typing an unsent message in an existing Slack composer

For example: **Type "Hello from Monster" into the current Slack message box. Do
not send.** Leave the desired workspace/conversation visible; if convenient, focus
the composer before activating Monster. No Slack API, DOM or accessibility tree
is used. If message content/topic is missing, the model should ask what to type.

The intended sequence is focus the existing composer if needed, type once, verify
that exact text in the editable composer (not an existing chat message), then ask
for final confirmation. For recognized type/write/draft/prepare message tasks,
Enter-to-send and send/submit proposals are rejected. Shift+Enter is available
for a requested line break. This is an English task-intent rule, not a proof of
safety for every language, application or click target.

Each planned input declares `expected_result`. After insertion, a separate
structured visual check reports `succeeded`, `failed` or `uncertain` with evidence.
It uses the same selected vision model, so it can still be wrong; Windows accepting
input and model verification are distinct signals. Inspect the actual composer.

The planner retains bounded recent action payloads and results in RAM. A failed
click cannot be repeated by shifting coordinates a few pixels or by waiting for a
caret/clock to change the PNG hash. Already-inserted text cannot be blindly typed
again in the same task. A failed text check stops incomplete; an uncertain text
check asks the user to inspect the intended field and confirm the **entire task**
or decline. Neither path automatically retypes the text.

Budget/deadline/no-progress stops explicitly say **Stopped incomplete**. That
outcome remains in the tray tooltip after the brief pill disappears. **Last
failure** includes a redacted action-result trace; **Last action preview** includes
the latest model verification evidence for local inspection. A task is completed
only after explicit user result confirmation.

Regressions exercise a scripted Slack-like draft sequence, wrong clicks followed
by coordinate jitter and changing screenshot bytes, duplicate typing, draft Enter
rejection, uncertain-result review and limit-failure semantics. These are fake
provider/input tests, not live Slack/Gemma qualification. A small vision model may
still fail to locate/read the composer; this update improves feedback and stops
invalid repetition rather than guaranteeing grounding accuracy.

Agent captures now use the foreground window's monitor rather than hardcoded
display 1. The current-composer draft workflow binds to that foreground window
and stops if it changes instead of redirecting text to another window. Monitor
selection uses only native focus/display geometry, not application text or APIs.
This change still needs a real mixed-monitor Windows check. The explicit CLI
`monster-tools screenshot --display N` keeps its requested display selection.

### Single-transaction draft typing

A recognized message-drafting task now permits one text transaction. After Windows
reports that transaction inserted, all further input proposals are rejected,
including different text, partial suffixes, Backspace and Shift+Enter. The model
must provide the complete requested draft in that first transaction, then verify
and request final confirmation. A task requiring correction must stop for manual
inspection; the agent does not edit or replay ambiguous input. This intentionally
does not support building a draft across multiple text transactions.

The prior duplicate guard compared text signatures, so a changed payload could
append to an already-inserted draft. Scripted controller regressions reproduced
that defect in both approval modes. These tests do not establish the cause of any
particular live Slack failure.

### Native qualification procedure for this workflow

Linux validation cannot qualify Windows input or Slack. No live Slack attempts or
model/verification latency measurements were possible in the cloud workspace:
the supplied private Ollama origin was denied by the configured proxy. Keep the
server private and test from the existing Windows LAN machine; do not change the
model to compensate for cloud connectivity.

1. Use a clean checkout of `main`, run `uv sync --frozen`, and quit Monster before
   standalone input-tool trials. Follow [Computer tools](COMPUTER_TOOLS.md) to
   qualify Unicode in disposable Notepad, physical coordinates on each monitor,
   negative origins/mixed DPI, emergency stop during typing and owned-input
   release. Inspect `uv run monster --capture-test isolated.png` locally for
   overlay exclusion; delete that opt-in artifact afterward. Do not infer native
   results from the portable suite.
2. Launch `uv run monster`. Retain `http://192.168.2.230:11440`, `gemma4:e4b`,
   context 16384, maximum output 4096, thinking off and keep loaded 5m. Use the
   existing connection/model probe controls. A failed connection is an environment
   blocker; a successful probe is not proof of grounding accuracy.
3. In a non-sensitive Slack test conversation, manually empty the composer and
   focus it. Activate Monster and request exactly: **Type 'Hello from Monster'
   into the currently open Slack message composer. Do not send.** Check that the
   pill closes and the intended Slack window/composer regains focus. Decline any
   proposal whose target or text is wrong. Inspect image-relative coordinates in
   **Last action preview** against the target display; do not use a Slack API,
   DOM, accessibility tree or clipboard to inspect the target.
4. Confirm success only if the editable composer contains exactly
   `Hello from Monster` once, no other field received text, no message appeared
   in chat history, and no Send/submit operation occurred. An accepted input
   receipt or successful model check is insufficient. Decline final confirmation
   for any mismatch, inspect manually and clear the disposable draft before a new
   trial. Never automatically retry uncertain typing.
5. Repeat five trials in each cell: focused composer/approval on, focused
   composer/approval off, visible but unfocused composer/approval on, visible but
   unfocused composer/approval off (20 attempts total). For the unfocused case,
   leave Slack foreground with focus in another harmless Slack field. Repeat on
   a secondary display after primary-display qualification. Restore an empty
   composer manually between attempts; do not send any draft.
6. In separate interruption trials press Ctrl+Alt+X during inference, review,
   typing and immediately after approval. Check that no queued input resumes,
   held keys/buttons are released, and a new task needs explicit authorization.
   Deliberately change foreground while a proposal is pending; verify it stops or
   rejects stale input rather than redirecting it. Test Decline for an uncertain
   result and confirm no replay.

Record only non-content metadata unless explicitly opting into a local screenshot
capture. For each trial record approval mode, initial focus, monitor/DPI, outcome,
wrong-target action count, duplicate insertion count, intervention and stop/cleanup
result. Time planning and verification separately with a local stopwatch; report
cold-load behavior separately. Classify failures at capture, grounding, proposal
validation, focus, dispatch, visible-result verification or completion. Distinguish
model proposals/verifier errors from tool/executor errors. Report successful drafts
as successes/attempts for each cell; do not count blocked or unrun trials as passes.
Screenshots and proposed coordinates must be inspected locally on that machine;
no personal screen captures, message contents or raw reasoning should be collected
by default.

### Initial focus handoff

For real-input tasks, Monster records the native foreground window identity
before opening the command pill. Submission hides the pill and explicitly
restores that window before starting the worker/model workflow. A missing target,
a Monster-owned overlay target, or denied focus restoration prevents startup;
focus the intended application and activate Monster again. Reopening an already
visible command pill preserves the original target. Dismiss, Stop and mode
changes discard it.

Current-composer draft tasks are bound to this pre-pill target, including their
first observation after the model probe. If foreground changes while inference
is pending, the controller stops rather than adopting the new window. Automatic
steps still do not restore focus after manual takeover. Native handles are used
only for focus infrastructure; no target titles, text or control trees are read.

Portable GUI regressions simulate the focus transition and verify restoration
occurs while the pill is hidden and before task startup, successful target
binding, denied restoration, missing handles and Monster-owned targets. They do
not establish that Windows accepts restoration or that Slack restores the desired
child editor's caret. On Windows, first focus an empty composer, invoke
Ctrl+Alt+M, submit the exact draft task and confirm the caret/text returns there.
Then test a visible unfocused composer, both approval settings and foreground
changes during the initial probe. If Windows denies restoration, expect an
incomplete outcome with no started task; do not bypass the check.

### Operation timings for native trials

**Last action preview** and **Last failure** now include content-free elapsed-wait
summaries for model compatibility checks, planning, scene rechecks, visual
verification, input startup, input dispatch and cleanup. Each stage reports
completed/failed/cancelled counts, total seconds and maximum seconds. Timings are
kept only in RAM, reset for each accepted task and remain available after that
task ends. No screenshot, prompt, typed text or model evidence is added to the
timing recorder, and it writes no files.

These are controller-observed wall-clock waits, including scheduling/event-loop
and worker overhead, not isolated model inference or SendInput duration. The
compatibility check can contain multiple provider requests. Input dispatch
includes arming and disarming. Cancelled waits stop at cancellation; they are
included in total/max and do not measure when remote work actually stops. Pending
operations have no completed sample yet. A completed provider request or dispatch
wait does not prove correct grounding, successful verification, intended input,
or task completion. Check the actual visual outcome separately.

After each native trial, inspect the details locally and record these non-content
numbers alongside the trial outcome. Separate model compatibility/cold-load time,
planning and verification from input-worker waits. Existing action-preview details
can contain the proposed text and visual evidence, so share only the timing block
unless you intentionally choose to share those contents. Opening details during
an active task retains the existing manual-takeover behavior.

### Capture cancellation and timing

Delayed screenshots after input or a model screenshot request are now scheduled
on one owned, cancellable timer. Each scheduled capture is bound to its task
generation and expected phase. Termination cancels it; a callback from an old task
cannot request a screenshot while a new task is probing or planning. Captures are
requested only in observation, pre-dispatch recheck and post-input verification
phases. Portable regressions reproduced the old cross-task callback in both
scheduling paths and now check that neither a capture nor additional input occurs.

The existing timing summaries also include **Screenshot capture**. This measures
the wait from the controller's request through the observation/error callback,
including overlay removal and compositor settling, but excluding the separate
350ms post-input delay. Completed, failed and cancelled captures are counted
separately. A completed capture is not evidence of overlay-free pixels or correct
focus; inspect those on Windows. Late frames from cancelled tasks do not update
the ended task's timings.


### When the composer focused but the model reports failure

A visual model can miss the caret or focused border of an empty Slack composer.
For a recognized message-drafting task, a failed or uncertain click check now
pauses immediately for a human focus review instead of trying more coordinates.
The pill asks whether the intended editable composer gained keyboard focus.

Approve only if you observed that the click focused the correct composer before
the review pill opened. With keyboard approval available, the pill does not take focus during review.
The mouse-only fallback may take focus. If the click
missed, activated another field, or you cannot tell, Decline and focus the intended
composer manually before starting a new task. The prompt expires after 60 seconds;
Escape, Decline, Stop and expiry never authorize more input. This review remains
required with automatic routine steps enabled.

After explicit approval, Monster restores the reviewed foreground window and
takes a fresh screenshot. The next model request receives a separately labelled
human focus confirmation. Additional click/navigation proposals are blocked; the
model may propose the requested draft text or ask for help. The original failed
or uncertain visual result stays in the diagnostic trace. Human focus confirmation
is not model verification, text verification or task completion. Foreground binding,
scene rechecks, sensitive-action review, one text transaction, independent stop,
and final visible-result confirmation still apply. A failed subsequent text check
still stops without retyping.

Portable regressions cover a false-negative focus check followed by human review,
a fresh observation, one text insertion and final confirmation in both approval
modes. They also cover declined/expired/stopped reviews, stale responses, rejected
extra clicks, premature completion, foreground changes and failed text verification.
The reported Windows behavior came from user observation; native recovery with
Slack still needs validation.

On Windows, reproduce the exact draft task. If focus review appears after a click
that you saw focus the composer, approve that focus review, inspect any subsequent
text proposal, and verify the draft appears exactly once and remains unsent. Only
then approve final completion. Record whether the focused child editor/caret is
restored after the review pill closes. Also test Decline and Ctrl+Alt+X at focus
review; neither should type or issue another click.

### Review binding and clarification handoff

Each input proposal is now deep-copied before review/recheck. The controller
compares the current proposal with that snapshot both when beginning recheck and
immediately before dispatch. A changed action, payload or proposal metadata stops
incomplete; approval cannot authorize a different valid text payload or an
in-place mutation of the original action dictionary. Automatic routine steps use
the same binding through their scene recheck. The snapshot stays in RAM and is
cleared when the task ends; no payload is added to shared diagnostics.

A real clarification reply now restores the previously observed target window
before a fresh capture, just as an explicit action review does. This prevents the
clarification pill's focus from being adopted as the task target. Restoration and
foreground checks remain required, and ordinary automatic steps still do not
restore focus after manual takeover. Escape/dismiss cancels directly without
submitting a fake clarification reply or requesting a new screenshot.

Portable regressions reproduce changed-proposal dispatch, clarification focus
loss, and the extra capture formerly triggered by dismissal. They cover exact
binding in both review and automatic modes, replacement/in-place mutations before
approval and during recheck, restored clarification focus, and direct dismissal.
Native focus restoration still requires a Windows check: answer a clarification
while Slack is the task target, then confirm the original composer receives the
single reviewed text insertion. Dismissing that clarification must stop without
another action.

### Diagnosing app-opening failures

The human composer-focus review applies to recognized message-drafting tasks, not
commands such as **open sticky note**. App-opening tasks still use the general
bounded recovery policy. Three failed click checks in that workflow do not by
themselves indicate that the draft focus-review code was bypassed.

**Last action preview** now shows the proposed expected visible result. It also
pairs the last visual verification with the specific action and expected result
that were checked, even if a newer proposal has since arrived. Compare that
condition with the verification evidence and what you actually saw. For example,
opening Start should be checked against the visible Start menu, not against the
final application already being open. This diagnostic display does not establish
which condition caused an earlier failure; native screenshots/observations are
still needed to distinguish wrong grounding from a false-negative verifier.

These details remain local/in memory and may contain proposed text or screen
information, as the existing preview already does. Share only non-sensitive
parts. The content-free failure trace continues to omit those payloads. No limits
or input policies were relaxed for app-opening tasks.

### Explicitly closing the current window

A narrowly recognized instruction such as **Close the current Chrome browser**
or **Please close the active window** now permits one **Alt+F4** transaction.
The controller binds the target foreground window before dispatch and requires
human review even when routine-step approval is off. Inspect the actual window
and any unsaved work before approving. This targets the current window, not every
Chrome window/process, and does not read titles, application APIs or control trees.

For these recognized tasks, guessed title-bar clicks and other input operations
are rejected. The controller will not issue a second close, click through an
unsaved-work dialog, or automatically dismiss confirmations. It observes the
result and requires final visible-result confirmation. A confirmation dialog,
blocked close or ambiguous outcome must be inspected manually. Unrelated,
negated or bulk-close instructions do not authorize Alt+F4. Recognition currently
covers a narrow set of English current/active/foreground-window phrases; it is
not a general natural-language permission parser. Ctrl+W/tab closing and closing
background/all windows are not added by this change.

The worker checks focus through the chord's final key-down. Closing may itself
change foreground; afterward only worker-owned key releases remain. Those releases
no longer misclassify that expected transition as a partial outcome. A focus change
before the final key-down still aborts and cleans up. Portable tests simulate both
cases, mandatory review, decline, target changes, unsupported intent and blocked
repeat closes. They do not qualify actual Chrome/Windows behavior.

To qualify on Windows, use one disposable Chrome window with no unsaved form data.
Request the exact phrase above. Verify the Alt+F4 proposal includes an unsaved-work
warning, Decline once to confirm no close, then start a new task and approve only
the intended window. Confirm the one window closes, no other window closes, and
no Alt/F4 remains held. Test emergency stop separately. Do not use personal work
or unsaved forms for initial qualification.

### Rejection diagnostics

Failure traces now distinguish invalid model responses, repeated/previously failed
input, tool/action validation and unsupported completion. Named key presses and
chords include their validated key names (for example `press WIN` or
`chord ALT+F4`); unknown key payloads are omitted. App-opening/window-closing failures
now direct the user to inspect the target and action preview rather than telling
them to focus a message composer. These categories do not prove why an older run
failed; inspect the proposed action, expected result and visual evidence as well.


### Approve without moving the pointer

While a review is pending, press **Ctrl+Alt+Y**, then release all keys, to approve
that one request. With this global shortcut registered, supervised approval,
focus-review and final-confirmation pills appear without requesting foreground
focus. The pointer stays where you left it. No cursor restoration or target input
is performed just to approve. Ordinary text clarification still intentionally
focuses the pill for typing.

The shortcut hint appears only when registration succeeds. Conflicts with another
Monster shortcut or an OS shortcut leave the existing mouse approval available;
no conflicting binding is silently replaced. Ctrl+Alt+X remains the independent
worker stop during real-input tasks. Plain Enter is not an approval shortcut.

Keyboard approval waits until Y, modifiers and mouse buttons are released before
responding. It is bound to the displayed request and controller; replacement,
expiry, Stop, dismissal, disabled/oversized review and mode changes invalidate a
queued gesture. Native hotkey IDs are not reused for new registrations, so a stale
hotkey event cannot be delivered as a newly registered request. When the intended
window is already foreground, restoration avoids calling SetForegroundWindow
again, reducing unnecessary disruption to transient UI.

Moving the pointer to a mouse approval button can change application hover state,
and taking focus can dismiss menus. Monster still hides overlays and takes a
fresh screenshot; its scene checks are not relaxed. Pointer-free approval reduces
those sources of interference but does not prove native menu/focus preservation.
Qualify on Windows using a disposable editor and an open Start menu: leave the
pointer still, read the review, press/release Ctrl+Alt+Y, and verify no approval-
related pointer movement, no held modifiers, no lost target and exactly one
reviewed action. Test Stop while holding the gesture, replacement/expired reviews,
and a shortcut conflict; none should authorize stale input. Native results remain
unverified by the portable UI and fake-Win32 tests.

## Self-contained run diagnostics

The local **Last failure** details now include a content-bearing run report. Tray
→ **Last run report…** also works for successful/cancelled runs and failures before
the first model proposal. Original task text is retained after controller cleanup,
so the report itself answers which instruction, model, policy and runtime were
used. It records the original input and flags any truncation to the effective
instruction used by execution. No inference about the root cause is fabricated:
observations, accepted input, model claims and human confirmations remain distinct.

The versioned `monster.run.v1` JSON contains:

- Run ID, UTC start/snapshot timestamps, ordered event sequence and elapsed times.
- Parent/worker Git revision where available, Python-source fingerprints captured
  at import/startup, platform/dependency versions and source-mismatch evidence.
  A revision alone does not establish a clean checkout; the fingerprint identifies
  the source tree. Missing/incomplete fingerprints are marked rather than assumed.
  Background/worker diagnostics are bound to originating jobs and runs; stale payloads
  are excluded and marked as ignored rather than contaminating a later report.
- An allowlisted configuration: server origin without userinfo/path/query, model,
  context/output budgets, thinking, keep-loaded, timeout and approval settings.
- Original/effective task, clarification replies, actual planner context/history,
  each parsed proposal, expected visible result and exact policy-rejection reason.
- Stable observation IDs, SHA-256, physical bounds/display geometry, age and
  per-run foreground aliases. Dispatch links its planning and recheck frames,
  image-space action, mapped physical action and actual worker focus/geometry binding.
- Mouse receipts with observed physical cursor position when available, accepted/
  rejected/partial outcomes, native exception class/project stack and cleanup status.
  Cursor observation failure is recorded without changing/replaying the input outcome.
- Review prompt/response, mouse-versus-keyboard approval, release/cancellation events,
  passive focus state, shortcut-registration errors, Qt logical pointer/screen coordinates and device-pixel ratio.
  Logical Qt positions are labelled separately from native physical coordinates.
- Capture overlay visibility/settle facts; scene-recheck reason and sampled change
  counts/thresholds; paired post-action verification status/evidence; terminal stage,
  pending operations, primary failure and any separate cleanup failure.
- Provider HTTP status/route, transport exception class, finish reason, available
  token counts, exception-cause classes and server timing fields. Known worker-stop
  reasons distinguish focus/geometry/lease changes, held-input expiry and emergency
  stop without recording arbitrary exception messages. Ollama duration fields retain their native
  nanosecond units. Parsed decision fields are allowlisted and bounded. Native
  `message.thinking`, arbitrary response bodies, exception values/locals, environment
  dumps and credential files are not collected.

### Review and export

The details view offers **Save report…** (JSON) and **Save with screenshots…**
(ZIP with `report.json` and `screenshots/frame-NNNN.png`). Exports use the displayed
frozen run snapshot, so a later run cannot silently replace the report being saved.
Existing files are not overwritten. There is no automatic save, upload or message
sending. Opening a live run report stops that run and waits briefly for cleanup;
if cleanup remains pending, the snapshot says so rather than claiming it succeeded.

Task/proposal/evidence text and recent native screenshots remain in RAM. Image
retention is bounded to 16 recent frames and 24 MiB per run, events to 512, and
observation metadata to 128. Counts of evicted/omitted evidence are explicit. The
most recent failure snapshot remains available until replaced or process exit;
the current run recorder is replaced by the next run. Screenshots are written
only by the explicit screenshot-export action. A screenshot-free JSON export does
not include PNG bytes. The existing `failure_diagnostics()` API remains redacted;
the new full local report deliberately includes the task/action text requested
for diagnosis. Review it before sharing, especially screenshots of other applications.

After a failure, export one screenshot ZIP before starting another task and share
that artifact for analysis. Its instruction, proposals, reasons, coordinates and
paired images are intended to remove follow-up context questions such as “what
instruction did you give?”. The evidence can identify a policy block, unsupported
shortcut, target change, coordinate discrepancy, false-negative verifier, transport
failure, token truncation or cleanup problem. It does not guarantee that every
failure can be proven from bounded captures; unknown/omitted evidence stays marked.
A process crash/restart loses unexported RAM evidence.

Automated validation uses synthetic observations, fake input and HTTP transports.
It checks report completeness, negative-origin coordinate mapping, immutable
snapshots, explicit-only persistence, bounded retention, credential/raw-thinking
exclusion, startup failures, primary-versus-cleanup failures and old-run callback
binding. Native Windows/Chrome/Slack behavior still requires live qualification.

### Explicit model coordinate units

Live Ollama mouse proposals now use `x_1000` and `y_1000`: integer coordinates in
0–1000 across the entire supplied screenshot. Top-left is (0,0), bottom-right is
(1000,1000). The adapter converts once using `round(value * (size - 1) / 1000)`
to native image pixels. The controller then adds the captured monitor origin;
it does not apply Qt device-pixel-ratio scaling. Local input-tool APIs still take
native physical coordinates. Trusted legacy parser callers can explicitly retain
image-pixel mode; live Ollama requests always use normalized mode and reject old
or mixed coordinate keys instead of guessing their units.

`Decision.coordinate_source`, the action preview, and run-report proposal/mapping
events retain the original normalized values, image dimensions and conversion.
Canonical actions sent to history, scene checks and verification use native image
pixels, as those prompts now explicitly state. Native cursor receipts remain the
independent observation of where Windows actually placed the pointer.

A supplied 1920×1080 incident report showed model proposals at y=955 labelled as
taskbar clicks, while the taskbar was around y=1050. All native cursor receipts
matched the requested pixels and the terminal stayed foreground. This establishes
bad model target coordinates rather than a native DPI offset or a false-negative
verifier in that run. The pattern is consistent with normalized-coordinate output
under the old pixel-only prompt; it does not prove the model's hidden reasoning.
The new self-describing field names remove that unit ambiguity without silently
reinterpreting older outputs or hardcoding an application's icon position.

Tests cover that incident's coordinate range, multiple resolutions, edge values,
malformed/mixed units, strict live-provider schema, and negative monitor origins.
They prove the transformation/validation contract, not that a model can locate
every icon. After updating and fully restarting, repeat the task and inspect the
new mapping plus before/after images and native cursor receipt. A further failure
can still be a grounding error; verification, reviews, stop and limits remain in
force. General window-task recovery no longer asks the model/user to focus a
message composer.

Explicit requests ending in “click … once” have a deterministic one-click input
budget in both approval modes. Only pointer movement and one single click are
allowed; keyboard, text and scroll substitutes and double-clicks are rejected.
After that click, no further input is allowed. Failed or uncertain visual
verification stops incomplete immediately, without another target guess. A
successful visual check still requires final user confirmation. Compound tasks
such as “click once, then type hello” use the ordinary supervised workflow.
This budget does not establish that the model located the right target.
