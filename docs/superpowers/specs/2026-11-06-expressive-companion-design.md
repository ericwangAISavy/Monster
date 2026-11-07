# Expressive, cute companion

User requests a polished, cute futuristic companion with sleep, thinking and work
recognizable from the character itself. Explicitly no companion text, text panels
or spinning circles. User instructed continuing implementation. Keep existing
command/approval functionality, diagnostics and capture/input safeguards.

Chosen implementation: retain native PySide6 vector rendering and the Monster
horned silhouette; redesign as a rounded pearl/lavender creature with dark glass
face, expressive eyes and paws. Sleep: tucked body/closed eyes/slow breathing.
Think: head tilt, upward gaze, paw to temple and small diamond glints. Work:
forward lean, alternating tapping paws and short directional light strokes.
Ready/review/done/error retain separate postures. No orbit/spinner or visible state
badge/tooltip. Screen-reader names keep status semantics without visible text.

Alternatives considered: a larger dashboard conflicts with user constraints;
video/sprite assets add scaling/performance burden. Existing procedural rendering
supports DPI scaling and reduced motion without dependencies. Assistant selects
these implementation details under user's instruction to continue.

Use an 80×80 logical-pixel footprint at 100%, retain 75–150% scaling and optional
corner placement. Reduced motion freezes animation while preserving poses. Paused
uses a still waiting pose, request activity restores to prior task state. Maintain
click-through/no-focus flags and hide both existing surfaces for real capture.
