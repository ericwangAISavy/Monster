# Signed Windows overlay renderer

The user's request is visibility above Start/Search, not parking beside it.
Ordinary Qt/topmost windows cannot guarantee that. Implement an optional separate
signed UIAccess renderer; keep the controller and input worker at ordinary trust.
No undocumented window bands, token impersonation or administrator agent process.

Only appearance/state/visibility travels through a bounded local shared-memory
mailbox. No model payloads, screenshots, instructions, filesystem paths or target
input operations are accepted. The child follows the real cursor and renders the
existing character. Its UIAccess token is verified; untrusted installation paths
are rejected. No existing companion configuration/model is silently changed.

Capture requires a fresh hidden acknowledgement or confirmed helper exit. A lost
heartbeat hides/exits the helper. Shutdown closes the mapping/process. The normal
companion remains the fallback when the explicit signed helper is unavailable.
Add operator-only Windows build/sign/install instructions; don't disable signing
or secure-path enforcement and don't run these privileged install steps here.

Also fix the supplied Explorer-close report by recognizing its explicit foreground
wording, rejecting the incorrect digit before review, and preserving one reviewed
Alt+F4 with foreground binding and no close replay.

## Portable validation

520 tests passed; three native Windows tests skipped in Linux. Ruff, frozen
installation and normal/unsupported-helper offscreen startup checks passed.
Independent review found no code blockers. Protocol tests cover corruption,
appearance-only validation, token-vs-ready distinction, hidden-transition binding,
heartbeat loss, transient torn acknowledgements, pause/activity state, capture
refusal and import isolation. Explorer tests reproduce the report's wording and
reject the digit before one reviewed Alt+F4. Signing/install scripts were not run
(PowerShell/Windows/certificate unavailable); no signed executable was generated.
Actual token grant, frozen Qt startup, above-Start pixels, capture exclusion and
process cleanup remain explicit Windows release gates in WINDOWS_UIACCESS.md.
