# Windows Start/Search overlay: signed visual renderer

**Status:** source integration and portable protocol/capture tests implemented.
The frozen Windows build, signature/trust chain, token grant and actual visibility
above Start are **not qualified in the Linux workspace**. No executable/certificate
has been produced or installed here. Ordinary `uv run monster` still cannot promise
visibility above Windows shell bands. Repeated topmost flags and parking beside
Start are a fallback, not the requested above-menu mode.

## Architecture and trust

Use a separate **visual-only** `MonsterOverlay.exe` with an `asInvoker`,
`uiAccess=true` manifest. The existing Python agent, model requests, approval pill
and isolated input worker remain ordinary processes. Do not add UIAccess to the
agent/Python interpreter, run the agent as administrator, disable Windows trust
checks or use undocumented window-band APIs to achieve this.

The explicitly configured renderer must resolve beneath the machine's Program
Files directories and have the exact executable name. Windows' supported shell
launcher starts this one helper. The parent queries the **actual process token's
UIAccess flag**; the child also refuses to render without it. A ready message is
not enough. There is no model-controlled executable, URL, argument or launch path.
No credentials are stored or passed. Windows requires a valid trusted signature
and secure installation for UIAccess; a manifest or unsigned build alone is not
sufficient.

Only bounded appearance/state/visibility JSON crosses a local, randomly named
shared-memory mailbox: enum mood, built-in character, size, cursor/corner setting,
offsets and booleans. No task text, screenshots, arbitrary paths, model replies,
keyboard/mouse operations or code are accepted. The helper imports no controller,
model provider or input worker, owns no emergency hotkey and has no network API.
Its frozen bootstrap clears inherited Qt/import plugin overrides and uses the
bundle's plugin directory. This is a small reviewed visual protocol, not a sandbox
against a hostile administrator or malicious software running as the same user.

The helper follows the cursor with the existing animation and ordinary topmost
calls under its legitimate UIAccess token, without the beside-shell placement
used by the ordinary fallback. This is the supported path intended to remain
above Start/Search on the normal interactive desktop. It does not promise drawing
on UAC/password/sign-in secure desktops or overcoming OS/compositor stalls.

## Build on Windows 11 AMD64

Use a reviewed checkout and a normal PowerShell window:

```powershell
uv sync --frozen
.\packaging\windows\Build-Overlay.ps1
```

The script uses Python 3.12 in a separate `.overlay-build-venv` and a pinned
PyInstaller build tool. It does not replace the application's Python 3.15/other
existing `.venv`, modify `uv.lock`, or bundle personal reports/screenshots. The
unsigned output is `artifacts\overlay\MonsterOverlay`. Source/Qt/build compatibility
still needs a real Windows build; generated binaries are ignored by Git.

## Sign and install explicitly

A usable code-signing certificate with a Windows-trusted chain and its private key
in the operator's certificate store is required. Do not send the private key,
password or PFX to the assistant or commit it. The script does not create trust
roots, change UAC/UIAccess policy, or bypass certificate checks. An untrusted
self-signed certificate is not a production signing setup. Coordinate actual
certificate/trust provisioning with the Windows administrator when needed.

From an elevated PowerShell **for this installation step only**:

```powershell
.\packaging\windows\Install-Overlay.ps1 -CertificateThumbprint YOUR_CERTIFICATE_THUMBPRINT
```

Optional `-TimestampServer` is an operator-selected signing service. Without it,
no timestamp endpoint is contacted. The installer signs the built executable,
requires a valid signature and copies the reviewed bundle to
`Program Files\Monster\Overlay`. It refuses to overwrite an existing installation;
review/archive/remove an old install explicitly first. Inspect the installed
signature before use. A valid Authenticode result alone is not claimed to prove
Windows will grant UIAccess; runtime token validation remains mandatory.

## Launch the agent normally with the renderer

Close the elevated installation shell. From a **normal**, non-administrator shell:

```powershell
uv run monster --overlay-helper "C:\Program Files\Monster\Overlay\MonsterOverlay.exe"
```

Adjust the drive only if the machine uses another Program Files location. There is
no automatic download, signing, installation or helper-path preference change.
Without the flag or when launch/token/readiness checks fail, the ordinary animated
companion remains the fallback. UI diagnostic events now include
`overlay_visibility`: backend, helper configuration and a safe failure category.
A report saying `ordinary_topmost` is not evidence the above-menu renderer is active.

## Capture and failure handling

The local companion becomes a transparent proxy only while the renderer is ready
and its token is verified. Before screenshot capture it publishes a hide transition.
Capture refuses to run unless that transition has a current hidden acknowledgement
or the helper has been confirmed to exit. A topmost timer cannot revive a hidden
renderer. The native capture-affinity hint is additional protection, not permission
to skip hiding. Brief capture disappearance is still intentional.

The helper hides/exits after a lost parent heartbeat. Torn mailbox snapshots are
ignored rather than executed. Missing/stale acknowledgements fall back and
terminate the visual-only helper; shutdown closes the process and mapping. If
helper shutdown cannot be confirmed, capture stays blocked. Input stop/lease and
owned-key cleanup remain inside the original ordinary input worker. No target
input is replayed by the renderer mechanism.

## Required Windows evidence before claiming success

After a signed installation, explicitly opt into token/IPC lifecycle tests:

```powershell
$env:MONSTER_UIACCESS_HELPER = "C:\Program Files\Monster\Overlay\MonsterOverlay.exe"
uv run pytest -q tests/test_overlay_windows.py
Remove-Item Env:\MONSTER_UIACCESS_HELPER
```

These tests launch only Monster's own visual helper; they do not operate target
apps or capture personal content. Token/Qt-visible acknowledgement does not prove
physical pixels or z-order. Manually verify:

1. Open Start/Search and move through its search bar, results and the taskbar.
   The configured UIAccess character should remain above the menu near the cursor,
   continue sleeping/thinking/working status and never steal search keyboard focus.
2. Test all supported sizes, reduced motion, each monitor and 100/125/150/200% DPI.
3. Run `uv run monster --overlay-helper "C:\Program Files\Monster\Overlay\MonsterOverlay.exe" --capture-test unique-test.png` using
   disposable screen content. Inspect actual pixels: neither Monster overlay should
   appear. Delete private test captures after review.
4. Stop/kill the helper and confirm ordinary fallback, safe capture failure when
   hiding cannot be acknowledged, and no lingering overlay on app shutdown.
5. Exercise Ctrl+Alt+X during a benign task; the original input worker must stop
   independently of the visual helper or model request.

Until those checks pass, this is an experimental deployment path, not a claim
that always-visible behavior was established by Linux simulations.
