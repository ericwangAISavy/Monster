# Expressive companion implementation plan

Goal: replace badges/spinning ring with cute, legible animated character poses.
Spec: docs/superpowers/specs/2026-11-06-expressive-companion-design.md.

- [x] Regression tests: no companion text widgets or drawn text/spinner; sleeping,
  thinking and working poses distinct; reduced-motion states stable; request/pause
  state restoration, scaled placement and negative monitor geometry preserved.
- [x] Extract renderer to ui/character.py; implement pearl shell, glass face,
  dimensional lighting and state-specific eyes/body/paws. Keep draw_monster port.
- [x] Remove status badge/tooltip, retain accessible state names, reduce footprint,
  map paused state to waiting. Preserve communication pill and capture isolation.
- [x] Render/inspect state sheet and animation frames; correct visual defects.
- [x] Update docs, full pytest/ruff/startup, independent code review and normal
  commit/push under standing user authorization.

Validation: 289 tests passed in 37.61s, ruff and diff checks passed, offscreen
startup exited 0. Independent review found no blockers, passed 36 focused tests
and checked 588 frames across states/75–150% sizes without clipping. State sheet
was inspected on dark/light backgrounds and a 120-frame motion preview generated.
Native Windows compositor/focus behavior was not requalified by these portable
checks; existing capture and input safeguards are unchanged.
