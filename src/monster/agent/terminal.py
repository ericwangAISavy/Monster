"""Content-free, post-cleanup evidence from one supervised controller generation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionReport:
    run_generation: int
    terminal_kind: str
    confirmed_input_transactions: int
    input_may_have_occurred: bool
    cleanup_confirmed: bool
    visual_verified: bool
    human_confirmed: bool
    observation_ref: str | None
