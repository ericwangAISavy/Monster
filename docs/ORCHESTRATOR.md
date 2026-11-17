# Orchestrator: plan first, then act

Monster reasons at two levels. The **orchestrator** never touches the mouse or keyboard:
it splits the task into small subtasks and decides what to do when one fails. The
**step loop** (the existing `SupervisedController` loop) carries out one subtask at a time
with screenshots, mouse and keyboard, with all of its checks unchanged.

```
task ─► plan(task, screenshot) ─► [subtask 1, subtask 2, …]      (1–8, each: instruction + done_when)
          │
          ▼ for each subtask
        step loop: observe → decide → validate → recheck → act → verify  (own budget)
          │  complete → independent whole-subtask check
          ▼
        succeeded and more remain? ─► next subtask (no extra model call)
        failed (recoverably) or last one? ─► assess(task, plan, results, screenshot)
                                             next | revise remaining plan | done (final answer) | stop
```

Turn it on or off in **AI model & automation → Plan tasks as steps first**. It is on by
default for saved settings and works with either intelligence provider (Ollama or Claude Code).

## Model calls

| Call | Input | Output |
| --- | --- | --- |
| `plan` | Task, current screenshot | 1–8 subtasks, each with a visible "done when" condition |
| `assess` | Task, plan, results so far, current screenshot | `next`, `revise` (new remaining subtasks), `done` (final answer), or `stop` |

Both prompts share `ORCHESTRATOR_GUIDE` in `providers/ollama.py`: one app and one small goal
per subtask, launch apps through Start, open websites by typing the bare domain, keep
"open a menu and pick from it" in one subtask, end information tasks with a reading step,
never add sensitive steps, and never act inside the app that controls Monster.

## Approval

- **No approval:** the plan runs straight away; the final answer is reported when the
  last review says `done`.
- **Laptop approval:** the pill shows the plan once to approve, step-level reviews follow
  the usual rules, and the final answer is confirmed once. Subtask completion is checked
  by the independent whole-subtask check instead of asking you after every step.

## Limits

| Limit | Value |
| --- | --- |
| Subtasks started | 8 |
| Plan revisions | 2 |
| Inputs per subtask / per task | 8 / 30 |
| Planning calls per subtask / per task | 15 / 60 |
| Task duration | 10 minutes |

## Which failures can be re-planned

Only failures that leave no uncertain input state end just the current subtask: no
verified progress after three attempts, three rejected proposals, the model being unable
or needing clarification (No approval), a canvas that could not be established, an app
window that did not appear, an unverified drawing stroke, and an unconfirmed subtask
completion. Anything that may have left input in an unknown place (typed text that could
not be verified, partial input, cleanup problems, a single-click task) or a stop from you
still ends the whole task immediately.

## Related protections

- **Protected apps:** input that would land in Visual Studio Code, Cursor, Claude or
  ChatGPT (the apps that usually control Monster) is refused before dispatch unless the
  task names that app. Pressing WIN is allowed so Monster can leave them.
- `task_status` shows `plan` and `current_step` while a planned task runs, and
  `task_diagnostics` includes the plan, each subtask's result and each review.
