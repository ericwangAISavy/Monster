# Windows computer tools: manual qualification milestone

Monster now has real local **Screenshot, Mouse and Keyboard** primitives. Ollama/MCP are not required for these tools. The companion defaults to its original mock task controller and also offers opt-in Ollama screenshot/action preview. A separate experimental supervised controller can now connect reviewed model actions to these tools; see [Supervised execution](SUPERVISED_EXECUTION.md). The standalone harness remains a deliberate one-action test.

## Install and test

On Windows 11 x64, from this checkout with Python 3.12+ and uv:

```powershell
uv sync --frozen
uv run monster-tools --help
uv run monster-tools displays
```

For input tests, **quit the companion from its tray first**. The real-input worker must register **Ctrl+Alt+X** independently, and refuses to start if the companion or any other program already owns that shortcut. No administrator privilege or provider credential is required. Use standard-privilege applications in an unlocked interactive desktop. Elevated/secure desktop, remote/disconnected sessions and sensitive workflows are not qualified.

### Screenshot

```powershell
uv run monster-tools screenshot capture.png --display 1
```

Displays are 1-based; `--display 0` explicitly captures the complete virtual desktop. The PNG has native physical-pixel dimensions. A unique observation ID, monotonic timestamp, display rectangles and SHA-256 accompany images in the Python API. The CLI saves only the PNG, refuses overwrite and prints its bounds. Capture is explicit, never a continuous animation loop; screenshots may contain private desktop content, so inspect and remove test artifacts as appropriate.

`monster-tools screenshot` is a standalone capture with no control over windows in other processes. To test **Monster overlay exclusion**, quit any other instance and run:

```powershell
uv run monster --capture-test isolated.png
```

The companion opens its pill briefly, hides its surfaces through the existing capture-isolation transaction, captures the foreground application's display, restores visibility and reports the save. Open the PNG manually and verify that Monster and the pill are absent. The GUI remains open afterward. Capture does not overwrite an existing file. As before, native display affinity is requested but not assumed sufficient: the hide-before-capture fallback remains enabled. Other applications/other Monster processes cannot be hidden by this transaction. Do not open the tray/settings during the diagnostic.

### Mouse

Choose coordinates **inside an actual display** using the reported physical rectangles and the screenshot. Negative coordinates are valid on monitors to the left/above the primary. Coordinates in gaps between displays are rejected.

Examples below use `(400,300)` only as an illustration; choose a harmless target in your own layout:

```powershell
uv run monster-tools move 400 300
uv run monster-tools click 400 300
uv run monster-tools double-click 400 300
uv run monster-tools right-click 400 300
uv run monster-tools scroll 400 300 -120
uv run monster-tools scroll 400 300 120 --horizontal
uv run monster-tools drag 400 300 480 300
```

The worker moves the real pointer before coordinate-based actions. Wheel delta is in Windows units: ±120 is one notch; application behavior and horizontal scrolling must be checked visually. Drag has bounded incremental movement and guaranteed attempted owned-button release on interruption/failure. It rejects physical monitor gaps along the sampled path.

The Python action API additionally supports explicit `down`/`up` mouse operations. Held input is limited to two seconds; use atomic drag for ordinary dragging. No input operation changes the companion's animation directly—the renderer continues to observe the actual pointer.

### Keyboard

Open a disposable Notepad document manually. Run a command, then use the default **3-second countdown** to focus the editor and release all modifiers/buttons:

```powershell
uv run monster-tools type "Hello from Monster — Unicode works."
uv run monster-tools press ENTER
uv run monster-tools chord CTRL A
```

Choose a longer countdown when helpful:

```powershell
uv run monster-tools --delay 5 type "Another harmless test paragraph."
```

Commands target the **currently focused window**; they do not identify or focus Notepad through hidden application APIs. The harness does not validate that the intended editor has focus. Watch the countdown and keep tests non-sensitive. Do not type passwords/API keys into CLI arguments; your shell can retain them even though Monster's receipts omit text.

Named keys include A–Z, 0–9, F1–F12, ENTER, TAB, ESC, SPACE, BACKSPACE, DELETE, arrow/navigation keys, CTRL, ALT, SHIFT and WIN. Chords accept unique modifiers followed by one nonmodifier key. Use `press ENTER` for newlines; text payloads reject embedded control characters and lone surrogates. Unicode uses native UTF-16 input, not the clipboard. Supplementary characters/IME behavior still need real Windows qualification.

The Python API also supports `key_down`/`key_up` for modifiers with the same two-second hold limit. The manual CLI intentionally exposes only bounded transactions, not long-lived holds.

### Stop and receipts

**Ctrl+Alt+X** is registered inside the spawned worker's own Win32 message loop. It disarms input, invalidates the generation, and attempts to release every worker-owned key/button. No Qt event loop or model request is involved. Pressing it during the countdown aborts the subsequent command rather than silently rearming and executing. Ctrl+C exits the parent harness and requests worker cleanup.

The worker requires heartbeat traffic at least every second and has a hard 30-second arming lifetime. Geometry changes, inaccessible/locked desktop, malformed protocol, partial native failures, duplicate action IDs or lost parent communication disarm input. A fresh arm is explicit and refuses unconfirmed cleanup or physically held modifiers/buttons. No action is automatically retried.

Receipts distinguish `inserted`, `rejected` and `partial_or_unknown`. **Inserted means Windows accepted input events, not that the target application performed the requested action.** After ambiguous results, inspect the desktop before doing anything else. Clean shutdown waits for a worker cleanup acknowledgment; an unconfirmed or forced teardown produces a harness failure. Windows process death/OS stalls can still prevent release—inspect held state manually if cleanup is unconfirmed. No hard real-time or crash-proof guarantee is claimed.

## Implementation boundaries

| Module | Responsibility |
| --- | --- |
| `computer/contracts.py` | Strict operation/parameter allowlist, integer/finite bounds, physical rectangles and absolute coordinate normalization. |
| `computer/windows.py` | DPI awareness V2, current input desktop checks, display enumeration, reviewed `SendInput` structures, native mouse/Unicode/key events. |
| `computer/executor.py` | Input ownership, lease/generation checks before every event, serial bounded operations, interruptible waits and cleanup. |
| `computer/worker.py` | Separate process message pump, emergency shortcut, watchdog and one executor thread. |
| `computer/client.py` | Trusted synchronous session API with supervisor heartbeat/pipe thread, stop requests and shutdown acknowledgment. |
| `computer/screenshot.py` | mss native-resolution PNG capture, crop validation, dimensions/hash/observation metadata and size limits. |
| `computer/harness.py` | Intentional one-action CLI tests, countdown, receipts and error exit codes. |

The worker uses an **anonymous inherited multiprocessing pipe** rather than the originally proposed named pipe. This reduces the local listener/authentication surface for a single trusted parent. Messages are bounded JSON via `send_bytes`/`recv_bytes`, not deserialized pickle or generated code. There is no network listener, shell tool, browser DOM, accessibility tree, application API or clipboard tool. The worker is a reliability boundary, not a sandbox against malicious local programs. Use one session from one calling thread; concurrent API callers are unsupported.

`InputSession` is a trusted SDK boundary, not a direct model interface. A future agent must translate proposals into the allowlist and add observation freshness, target/focus evidence, policy, approval tokens, user-activity takeover and visual verification before dispatch. This milestone does **not** establish that arbitrary model-selected clicks are safe, that input can bypass Windows security, or that arbitrary human tasks are supported. The worker does not elevate privileges or bypass UIPI, UAC or secure desktops.

The parent supervisor keeps heartbeats running during a synchronous request; its automatic heartbeat is appropriate to this deliberate manual session, not an autonomous task lease tied to engine health. A future engine must additionally expire authorization on pause, task timeout or engine stall and synchronously disarm on cancel. No real input is connected to the existing GUI mock controller.

## Automated and native validation

Run:

```powershell
uv run pytest -q
uv run ruff check src tests
```

Portable tests use fake native primitives. They exercise strict rejection before input, negative-origin coordinate endpoints, actual display gaps, Windows structure sizes, Unicode surrogate event order, chord release order, partial failures, held-input expiry, heartbeat loss, geometry changes, stale generations, cancellation during text entry, screenshot metadata/crops and cleanup failures. Worker scheduling tests use actual bounded JSON pipes and a fake Win32 message pump, including independent hotkey interruption while text execution runs and hotkey-registration failure.

These tests do **not** call real SendInput or establish native screenshot fidelity. Required Windows checks:

1. Record Windows/Qt/mss versions, monitor rectangles and DPI configuration.
2. Capture display 1 and all displays; verify exact pixel dimensions, negative origins and cropped API output. Check black/locked/protected capture failure handling. The black-frame heuristic cannot detect every stale/protected capture.
3. Run `monster --capture-test` repeatedly and inspect saved pixels. Verify fallback restoration and no overlay feedback at 100/150/200% mixed scaling.
4. Exercise every mouse CLI operation on a harmless test surface, including drag/scroll/double-click. Verify physical coordinate accuracy across supported monitors and avoid monitor gaps.
5. Test ASCII, accented characters, CJK and emoji in Notepad; test navigation/chords and the supported keyboard layout. Record unsupported IME/application cases.
6. During a long harmless text command, press Ctrl+Alt+X. Confirm further insertion stops and no owned modifier/button remains held. Repeat under load; record timings rather than assuming the portable fake-backend timings apply.
7. Block/exit the parent or remove heartbeat in a dedicated test session; verify worker disarms/releases. Test hotkey conflict, monitor reconfiguration, session lock and native insertion rejection.
8. Confirm close/Stop does not issue new down events; any release uncertainty blocks rearming. Inspect input state after a forcibly killed worker.
9. Measure active capture/input CPU and latency on Windows; native enumeration and capture calls are synchronous, so qualify their worst observed behavior. Do not claim hard real-time interruption.

Native Windows checks are outstanding because this development environment is Linux. The next milestone after those checks pass is LAN Ollama integration and a supervised, observation-bound Notepad loop—not unrestricted autonomous control.

### Screenshot/request UI review

The UI entry point blocks capture while settings, reports or the tray menu are
visible. CaptureIsolation hides the companion and communication pill, waits for
the compositor, checks neither resurfaced, and then invokes the native capture
backend. The backend binds the foreground display, preserves physical dimensions
and monitor origin, and rejects a foreground change. The same PNG is base64 encoded
in the native Ollama `/api/chat` request; no GUI-size scaling is applied to input
coordinates. The overlays are restored before the asynchronous HTTP wait, so they
need not blink throughout inference. A brief disappearance during each actual
capture remains intentional. Windows capture exclusion still needs native
qualification; accepted display-affinity hints do not bypass hiding.

Synthetic settings checks use a generated three-color image and never invoke the
desktop capture gate. They activate the companion’s thinking pose. Task stages remain available in
accessibility descriptions and reports without visible status text. These are
execution stages, not model reasoning. Passive approval updates apply
final focus/input flags once, avoiding an intermediate native window recreation.

### Smooth pointer motion

Coordinate operations now ease from the observed physical cursor to the target
before pressing a button or scrolling. The planned approach is distance-adaptive
(about 0.18–0.8s), and drags ease over their existing bounded duration. Actual cursor
position is required; there is no teleport fallback if it cannot be observed.
Routes crossing monitor gaps fail before input. Pointer takeover, geometry/focus
changes where bound, stop or lease expiry abort the transaction. See
[Supervised execution](SUPERVISED_EXECUTION.md#comfortable-pointer-travel) for native
retest steps and limits. Keep inspecting receipts/visible effects; more move events
still constitute one transaction, and accepted insertion is not visual success.
