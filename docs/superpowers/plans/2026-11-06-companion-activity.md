# Companion activity implementation plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation. Steps use checkbox syntax.

**Goal:** Make busy/acting/review states legible, layout configurable, and request UI steadier.
**Architecture:** Extend existing companion rendering/preferences and bridge settings activity. Aggregate pill interaction flags; preserve capture isolation.
**Tech Stack:** Python 3.12+, PySide6, uv.
**Spec:** docs/superpowers/specs/2026-11-06-companion-activity-design.md

## Global constraints

- Preserve minimal companion/pill, passive status, review and independent stop.
- Keep both overlays hidden for real capture; synthetic tests use no desktop PNG.
- No default persistence of personal screenshots, task contents or reasoning.

## Review focus

- Old preference files load unchanged; invalid new choices rejected.
- Reduced motion preserves labels without animation.
- Negative monitor coordinates and scaling remain inside available geometry.
- Cancellation and dialog close clear busy indication without reviving stale requests.
- Passive approval keeps focus flags and click handling; mode changes do not churn intermediate HWNDs.

## Task 1: Companion rendering and layout preferences

Files: config.py, ui/settings.py, ui/companion.py, tests/test_ui.py, tests/test_geometry.py.
- [x] Write tests for preferences persistence/validation, readable distinct states, scaled sizes and anchored placement.
- [x] Observe failures, implement labels/ring and size/placement using existing renderer and geometry clamps.
- [x] Verify tests and inspect a rendered state contact sheet.

## Task 2: Request activity and stable pill interaction

Files: ui/provider_settings.py, ui/pill.py, app.py, tests/test_preview.py, tests/test_ui.py.
- [x] Test settings activity start/end/close, passive window flags, and absence of redundant passive-approval reconfiguration.
- [x] Observe failures; implement activity_changed(str), Companion.set_activity(str), app bridge and aggregate final flags.
- [x] Verify overlay-free capture and stop/dismiss existing tests remain passing.

## Task 3: Complete verification and delivery

- [x] Update user docs explaining states, settings and remaining capture hiding.
- [x] Run full pytest, ruff, diff checks and offscreen startup.
- [x] Request independent review, address findings, commit and non-force push to main.

## Completion evidence

259 tests passed with QT_QPA_PLATFORM=offscreen and permitted local event-loop
socket support; ruff and git diff --check passed. Offscreen startup exited 0.
The restricted reviewer environment timed out on async callbacks (including nine
existing tests); full-suite execution with the required local-socket permission
passed. Independent code review found no blockers and separately checked passive
mouse approval and scaled/negative-monitor placement. Generated state contact
sheet and 600×520 settings window were visually inspected. Windows native focus,
compositor behavior and mixed-DPI capture remain unverified.
