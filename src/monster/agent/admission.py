"""Host-selected review policy shared by standalone and mission controller paths.

This decision selects review/automatic/stop only. It grants no input authority;
validation, grounding, durable mission authorization and cleanup still apply.
"""

from dataclasses import dataclass
from typing import Literal

from .validation import approval_reason, window_close_requested


@dataclass(frozen=True)
class ActionAdmission:
    disposition: Literal['automatic', 'review', 'stop']
    reason: str | None
    policy_version: str = 'controller-review-v1'


def admit_action(decision, instruction, *, approve_each_step, hands_free, action_gate):
    """Apply existing review semantics using host settings, never provider flags."""
    if any(type(flag) is not bool for flag in (approve_each_step, hands_free, action_gate)):
        raise ValueError('Admission settings must be host booleans')
    if decision.kind != 'action' or not isinstance(decision.action, dict):
        raise ValueError('Admission requires an action proposal')
    automatic_mode = hands_free and not action_gate
    reason = (
        'Closing a window can discard unsaved work. Confirm the intended window '
        'is the current foreground target.'
        if window_close_requested(instruction) else
        'Per-step approval is enabled.'
        if approve_each_step and not automatic_mode else
        approval_reason(decision, instruction)
    )
    if automatic_mode and reason is not None:
        return ActionAdmission('stop', reason)
    if (automatic_mode or not approve_each_step) and reason is None:
        return ActionAdmission('automatic', None)
    return ActionAdmission('review', reason)
