"""Reviewed Phase 4 objectives; never populated from model/page text."""

import time
from uuid import uuid4

from .contracts import Mission, Subtask

TEMPLATE = "editor-six-v1"
PILOT_TEXT = {f"pilot-{letter}": f"Monster section {letter.upper()}. " for letter in "abcdef"}
PILOT_OBJECTIVES = {
    key: f'Type "{text}" into the currently focused disposable Notepad document. '
    "Preserve earlier sections. Do not send. Do not replace existing text."
    for key, text in PILOT_TEXT.items()
}


def editor_plan(
    *,
    mode="simulation",
    mission_id=None,
    budget=None,
    provider_ref="simulation",
    model_ref="fake-model",
    configuration_digest="0" * 64,
    clock=time.time,
):
    from .scheduling import MissionBudget, ScheduledPlan, canonical_digest

    budget = budget or MissionBudget()
    now = clock()
    tasks = [
        Subtask(
            subtask_id=key,
            objective_ref=key,
            application_ref="disposable-editor",
            success_refs=[f"visible-{key}"],
            category="local_reversible",
            depends_on=[f"pilot-{chr(ord(letter) - 1)}"] if letter != "a" else [],
        )
        for letter, key in zip("abcdef", PILOT_TEXT, strict=True)
    ]
    mission = Mission(
        mission_id=mission_id or "pilot-" + uuid4().hex,
        objective_ref=TEMPLATE,
        workspace_id="interactive-desktop" if mode == "supervised_pilot" else "simulation-desktop",
        authorized=True,
        deadline_utc=now + budget.max_wall_seconds,
        subtasks=tasks,
        max_delegations=12,
    )
    return ScheduledPlan(
        mission=mission,
        mode=mode,
        template_ref=TEMPLATE,
        budget=budget,
        created_at=now,
        provider_ref=provider_ref,
        model_ref=model_ref,
        configuration_digest=configuration_digest,
        catalog_digest=canonical_digest(PILOT_TEXT),
    )
