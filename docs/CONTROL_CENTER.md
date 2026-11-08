# Monster control center

Click or right-click the tray icon to open the sticky right-side panel. It stays
open when you click elsewhere; use its close button, Escape, or the tray icon to
hide it. Monster continues running. Quit is available inside the panel.

The panel groups task controls, companion visibility, settings, recent action and
failure diagnostics, and experimental missions/workspaces. Companion and model
settings, along with workspace settings, open inside the panel with a Back
button. Save and Cancel retain their existing behavior. Advanced mission tools
are collapsed by default. All controls use the existing application actions.

The panel anchors to the right edge of the display containing the pointer when
opened and uses its available desktop geometry to avoid the taskbar. The body
scrolls on smaller screens. Opening it pauses an active task using the existing
secondary-window behavior; it never silently resumes a task. Capture isolation
hides the panel before an agent screenshot, along with the companion and pill.

The design uses a charcoal surface, subtle separators, violet primary controls,
and a keyboard-accessible companion switch. The existing task pill remains the
input and approval surface. Windows tray focus, display scaling, and compositor
behavior still require a native Windows check; automated checks use offscreen Qt.
