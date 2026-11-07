# Cursor companion GUI

The everyday interface is two small independent Qt tool windows: a click-through vector creature and a compact communication pill. There is no main window, dashboard, task timeline, or background screen capture. Settings are opened intentionally from the tray.

This GUI direction supersedes the workspace/dashboard presentation in `ARCHITECTURE_PLAN.md`; that document's separation of models, tools, orchestration and policy remains applicable.

## Current functionality

- A miniature navy creature with cyan eyes, curved horns, subtle violet rim lighting and replaceable QPainter rendering. Approximately 44 DIP of body within a 60 × 64 DIP transparent window; DPI scaling is handled by Qt.
- Idle, listening, thinking, working, waiting, completed and error render states; restrained breathing, blinking and a reduced-motion option.
- Frame-independent cursor smoothing. The companion **only reads** `QCursor.pos()`; it cannot move the pointer or inject input. Its transparent, no-focus tool window uses Qt's native input-transparent window flag as well as widget mouse transparency.
- A 324 × 46 DIP pill with input selection, copy/paste, Enter submission and Escape dismissal. It stays fixed while typing, flips near screen edges, and uses the monitor's available geometry to avoid the taskbar when possible. Passive notices are noninteractive and do not request focus.
- Submission collapses the pill. Completion/status reuses it briefly. Approval and clarification reuse the same surface; no extra conversation windows. Escape rejects/cancels a pending question rather than granting consent. Approval is never automatically accepted with Enter.
- Windows native global activation, pause/resume and stop hotkeys; registration conflicts are reported and configurable. Tray offers the same actions, show/hide, settings, explicit approval/clarification demo fixtures, and Quit.
- A timer-based mock task controller simulates inference latency without blocking the UI. It performs **no AI inference, screenshots, mouse input, keyboard input, or real task completion**. Completion text, tray tooltip and settings identify the simulation. Native LAN Ollama settings and an opt-in screenshot/action preview are now available through the tray; see [LAN Ollama](OLLAMA.md). Preview never executes input.
- A capture-isolation API tries `WDA_EXCLUDEFROMCAPTURE` but always uses hide-before-capture fallback until a real capture backend is qualified. UI state is restored even on backend exceptions; an approval dismissed during capture is not restored.

## Run on Windows

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/), then run from this checkout:

```powershell
uv sync --frozen
uv run monster
```

Alternatively, the dependency specification supports a standard virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\monster.exe
```

The uv path uses the committed lockfile. The pip alternative resolves compatible versions instead of using that lock. No model server, API key or administrator permission is required for the GUI demo.

Only the creature appears at launch. Find Monster in the Windows tray (including the overflow area). Left-click its tray icon or press **Ctrl+Alt+M** to type an instruction. Enter starts the simulation; the capsule disappears and the creature changes expression. The completion notice explicitly says no computer actions occurred.

Default controls:

| Action | Shortcut / access |
| --- | --- |
| Activate | Ctrl+Alt+M or tray → Talk to Monster |
| Pause / resume | Ctrl+Alt+P or tray → Pause/Resume |
| Emergency stop | Ctrl+Alt+X or tray → Stop task |
| Dismiss | Escape while the pill has focus |
| Preferences | Tray → Settings |
| Exit | Tray → Quit Monster; Ctrl+C when launched in a terminal |

Activating during a running simulation pauses it for intentional manual takeover. It does not let two tasks run concurrently. Resume from the tray/shortcut, or Stop before starting another task. Closing settings does not automatically resume a paused task. The GUI does not globally monitor physical user input; automatic input takeover detection belongs to the future real control engine.

Hotkey conflicts appear in the startup tray notification. Use tray Settings to choose a different modifier+letter/digit/function-key combination. Duplicate bindings are rejected. On Windows a failed update restores the previous bindings; registration failures remain visible. Settings are non-secret JSON in Qt's per-user AppConfigLocation (`companion.json`). Invalid settings fall back to defaults with a notification, and are not overwritten until explicitly saved. Instructions are not logged or persisted.

## Code boundaries

| Module | Responsibility |
| --- | --- |
| `ui/companion.py` | Pure vector renderer, local animations, read-only cursor following. |
| `ui/pill.py` | Input, passive notice, approval and clarification presentation. |
| `geometry.py` | Testable Qt logical-coordinate placement, edge flipping and smoothing. |
| `lifecycle.py` | `TaskController` event port and explicitly simulated implementation. |
| `app.py` | Surface visibility, tray, focus requests, task event wiring and teardown. |
| `native.py` | RegisterHotKey, WM_HOTKEY dispatch, foreground request and capture-affinity hints. No input injection. |
| `capture.py` | Explicit hide/capture/restore transaction for a future screenshot service. |
| `config.py`, `ui/settings.py` | Non-secret GUI preferences; no second provider registry. |

To replace the simulation, subclass `TaskController` and inject it into `CompanionApp`. Implement submit/cancel/toggle_pause/respond and emit state, active, paused, status, finished, failed, approval_requested and clarification_requested signals. Emit from QObject worker signals (Qt queued delivery) when running inference off-thread. Never run inference inside UI slots. `submit` must return promptly; `cancel` must synchronously invalidate/disarm queued input before requesting asynchronous inference cancellation. Ignore stale request IDs and generations inside the engine.

A real engine must implement its own independent emergency-stop/input lease, sensitive-action policy, focus validation, user-activity takeover detection and post-action visual verification. This GUI's hotkeys are handled by the Qt event loop and are **not an independent input safety boundary**. Do not connect real input until that boundary exists. Models never approve their own actions. The current controller rejects concurrent submissions, stops timers on cancel and ignores stale approval responses.

The GUI uses device-independent Qt coordinates only. A future computer tool must use its own physical-pixel transforms; do not pass Qt window coordinates directly to SendInput. Qt normally enables per-monitor DPI awareness on Windows; verify the packaged executable's behavior and manifest on target machines.

## Capture isolation

Call `CompanionApp.capture_desktop(backend_callable)` on the GUI thread; this guards against visible settings and tray menus, then delegates to `CaptureIsolation.capture` and receive `completed(object)` or `failed(safe_error_category)`. The callable should perform only the short synchronous screenshot acquisition; encoding/inference belongs off-thread afterward. Do not call the backend before the completion handshake, do not schedule continuous captures for character animation, and never block the GUI waiting for an isolation signal.

The transaction hides both surfaces, flushes Qt window changes, waits at least 50 ms (100 ms default), invokes the backend, and restores prior visibility in a finally block. New activation is suppressed while capture is pending; pending questions are presented afterward. The application entry point rejects capture while the secondary settings dialog or tray menu is open. Future screenshot integrations must use this guarded entry point. Likewise exclude tray menus and any other Monster-owned surface before requesting an inference image.

A successful `SetWindowDisplayAffinity` call records only API acceptance. It does not prove that mss/GDI, Windows Graphics Capture, Desktop Duplication, remote software or a particular OS build honors it. The implemented fallback is exercised by automated tests, but its compositor timing and actual captured pixels still require Windows validation. If testing finds a backend can return stale overlays after the settle interval, fail that backend's qualification, add an explicit compositor/backend synchronization or increase a measured settle interval, and verify actual images. Do not silently feed contaminated screenshots to a model. A Windows mss capture backend is now available through the explicit `--capture-test` diagnostic; see [Computer tools](COMPUTER_TOOLS.md). Its native behavior still requires Windows qualification.

## Tests

```powershell
uv run pytest -q
uv run ruff check src tests
uv run monster --smoke-test
```

On headless Linux, set `QT_QPA_PLATFORM=offscreen` for pytest and smoke startup. This validates widget rendering and event logic, not Windows compositor/input behavior. The offscreen plugin may warn that tray/raise operations are unsupported.

Automated coverage includes edge placement on negative-origin monitors, smoothing, hotkey syntax, preference validation, cancellation and late callbacks, pause/resume, explicit/stale approvals, Enter/Escape input, status timeout replacement, stable pill positioning, collapsed execution, click-through flags, capture failure restoration and stop/question races during capture.

### Required manual Windows validation

Record OS build, Qt version, monitor/DPI layout and result for each row. These checks were **not executed on this Linux development machine**.

| Check | Procedure and pass condition |
| --- | --- |
| Click-through/no focus | Click, drag, scroll and select text through the companion's visible and transparent pixels. Underlying app receives input; companion never gains focus or a taskbar entry. |
| Focus/dismiss | Activate from hotkey and tray in several foreground apps; input gets focus when Windows permits, text selection/paste work, Escape hides it and desktop input remains available. If Windows denies foreground activation, click the visible pill; do not synthesize focus keystrokes. |
| Global shortcuts | Exercise activate/pause/stop with another app focused. Pre-register a conflicting binding, verify warning and settings recovery. Quit/restart and verify no leaked registrations. |
| Multimonitor/DPI | Check 100/125/150/200% mixed scaling, negative origins, taskbars on different edges, monitor unplug and resolution change. Companion follows only the real cursor; pill remains visible and stationary while typing. |
| Capture affinity | Test affinity acceptance and captured pixels for each chosen backend on supported Windows builds. Verify neither Monster surface is visible in the resulting frame. Do not substitute API return status for pixel inspection. |
| Hide fallback | Force affinity failure, capture repeatedly while both surfaces are visible and during state changes. Verify clean desktop pixels, correct restoration, no focus theft and no resurrected approvals after Stop. |
| Responsiveness/resources | Observe idle and moving-cursor CPU in Task Manager for 60 seconds each; repeat with simulated latency and reduced motion. Record averages/peaks rather than claiming an unmeasured CPU budget. Only tiny windows repaint; no screenshot polling. |
| Sleep/session transitions | Lock/unlock, display sleep and reconnect. GUI must remain usable; a future real engine must separately disarm input on those transitions. |
| Tray/settings/exit | Hide/show companion, change shortcuts/offsets, toggle reduced motion, cancel settings, exercise demo questions, quit during a task and capture. No lingering process or invisible input-capturing window. |

## Scope and known limits

The default mode implements the GUI with a mock lifecycle. Subsequent milestones add standalone Windows tools and opt-in Ollama inference previews; see [Computer tools](COMPUTER_TOOLS.md) and [LAN Ollama](OLLAMA.md). Preview remains disconnected from real input; the separate opt-in [supervised mode](SUPERVISED_EXECUTION.md) executes only individually reviewed actions. The vector character is an original temporary asset inspired by the supplied reference and can be replaced independently. No wallpaper/taskbar/reference background is copied. No native Windows behavior, GPU/CPU performance target or screenshot-backend compatibility is claimed as verified by the offscreen tests.

## Visibility around Windows shell surfaces

The enabled companion reasserts ordinary topmost z-order about every 250 ms and
on show, using native `SetWindowPos` without activation, resize, movement or
`SHOWWINDOW`. It remains click-through and does not take the Start/search
keyboard focus. Hidden/disabled widgets skip maintenance; screenshot isolation
still hides both overlays, so brief capture disappearance remains intentional.

Windows Start/search and some shell surfaces may occupy compositor bands above
ordinary topmost application windows. Monster does not use undocumented band
bypasses, elevation or UIAccess impersonation. For recognized shell window classes,
it reads only the foreground surface's rectangle/monitor identity as overlay
infrastructure, maps physical geometry to the matching Qt screen's origin/DPI,
and parks the character beside that panel while continuing status animation.
It does not read target titles, controls, contents or accessibility trees for this.
When a shell host exposes an unusable full-screen rectangle, a stable screen
corner is a best-effort fallback. Normal cursor following returns after it closes.

This cannot guarantee visibility above protected/secure desktops or every shell
implementation. On actual Windows, test opening Start, search and the taskbar at
100/125/150/200% scaling on each monitor, confirm search retains keyboard focus,
and verify both Monster surfaces stay absent from `monster --capture-test` PNGs.
Linux tests mock native calls and geometry; they do not prove real shell z-order
or visibility. No visible text panel or spinning indicator is added.
