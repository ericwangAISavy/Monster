# Phase 2 native Windows qualification — PENDING

No real Windows desktop, mouse/keyboard, foreground behavior, signed renderer or
LAN inference was tested in the Linux implementation workspace. Automated Qt
controllers are test doubles. **Implemented does not mean Windows-qualified.**

Use a disposable unlocked Windows 11 standard-privilege account/VM, non-sensitive
Notepad content, known-working selected vision model and a person present. Do not
change models or expose the LAN server. Use existing Screenshot/Mouse/Keyboard
paths only. Do not use application APIs, DOM or clipboard shortcuts for these tests.

## Prepare

```powershell
git pull --ff-only
uv sync --frozen
uv run pytest -q
uv run ruff check src tests
uv run monster
```

Confirm ordinary companion/pill, capture isolation and standalone supervised typing
still work first. Choose **Real computer control** in AI settings. Mission Mode
forces per-action approval independently of the saved checkbox; it never edits
that setting. Open an empty disposable Notepad document and focus its editor.

## Two-subtask smoke path (explicit opt-in)

1. Tray → **Experimental Mission · two Notepad drafts…**.
2. At mission A review, focus the editor and use **Ctrl+Alt+Y**. Expect a fresh
   screenshot and normal controller action review. Approve only a safe insertion
   of `Monster mission A. ` into that document. Never approve Send or other targets.
3. Inspect the actual visible text. Confirm final result only if A appears exactly
   once. Confirm that worker cleanup ends before B becomes eligible.
4. At mission B review, focus the editor again and authorize with **Ctrl+Alt+Y**.
   Approve only insertion of `Monster mission B.` without replacing A. Inspect
   both sentences, confirm B exactly once, and expect a completed mission.
5. Check **Last run report…** for the current controller's observations and receipts.
   Mission SQLite stores only references/metadata; export screenshots solely with
   explicit consent and inspect before sharing.

If a proposal/result is wrong, reject or press Ctrl+Alt+X. Do not approve repeated
text to force the test through. Report the actual partial/uncertain outcome.

## Interruption / restart / ownership matrix

- Deny B: no second insertion and no optimistic completion.
- Ctrl+Alt+X during model wait, action review, bounded input and final confirmation:
  no subsequent accepted input; owned keys/buttons release where the OS permits.
  Inspect physically if cleanup is unconfirmed. Record measured stop latency.
- Pause, Escape or settings/manual takeover: stop the outer mission too; pending
  approvals cannot later authorize input.
- Slow/disconnect Ollama: UI stays responsive; timeout/error pauses safely, no replay.
- Move the Notepad window or change focus/display geometry before action approval:
  existing freshness/focus/geometry checks must stop or reobserve; no stale click.
- Try a second Monster instance/manual task while one owns the session: loser must
  report busy and generate zero input. Do not run older versions alongside it.
- Quit while waiting or during input: inspect cleanup; do not restart a partially
  completed transaction as if no input occurred.
- To test restart after A without cancelling the whole mission, close the process
  only after A's cleanup has completed and B is waiting for review. Relaunch and
  choose **Resume latest experimental mission…**. A must not replay, and B must
  obtain a fresh subtask grant plus foreground/scene preflight.
- Crash at an ambiguous dispatch boundary only in the disposable VM. Reopen must
  pause uncertain, never repeat that effect. Explicit stop marks cancellation and
  deliberately does not offer automatic continuation.
- Above-Start rendering is a separate signed UIAccess qualification; ordinary
  topmost mode is not proof of it. Follow `WINDOWS_UIACCESS.md` if configured.

Record OS/Python/source revision, model/digest, hardware, display bounds/scaling,
exact commands, successful drafts/attempts, duplicate/wrong-target counts, latency,
cleanup proof and interventions. Screenshots require consent. Record app/tool errors
separately from model errors. One successful run is not production qualification.
