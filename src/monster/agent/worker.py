"""Bounded, read-only operational work; never an execution authority.

Each invocation does one inference, comparison or projection. ProviderJobs owns
threading/cancellation. The host owns captures, budgets, reviews, intent, input,
result interpretation and cleanup. No worker loop, journal or executor lives here.
Private images/context/payloads are RAM-only and excluded from packet reprs.
"""

from dataclasses import dataclass, field

from monster.agent.contract_adapters import grounded_from_checked, verification_from_legacy
from monster.agent.contracts import ActionPayload
from monster.agent.keyboard import terminal_window_requested
from monster.agent.validation import (
    prepare_action,
    scene_matches,
    validate_proposal_intent,
    window_close_requested,
)
from monster.computer.screenshot import Observation


@dataclass(frozen=True)
class VisualContext:
    model: str = field(repr=False)
    instruction: str = field(repr=False)
    observation: Observation = field(repr=False)


@dataclass(frozen=True)
class ProposalRequest:
    context: VisualContext = field(repr=False)
    history: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        if type(self.history) is not tuple or any(type(item) is not str for item in self.history):
            raise ValueError("Worker history must be an immutable snapshot")


@dataclass(frozen=True)
class VerificationRequest:
    context: VisualContext = field(repr=False)
    action: ActionPayload = field(repr=False)
    expected: str = field(repr=False)
    before_image: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_action(cls, context, action, expected, *, before_image=None):
        return cls(context, ActionPayload.from_action(action), expected, before_image)


class BoundedWorker:
    """Stateless operations only. Returned proposals/assessments grant no input."""

    async def propose(self, provider, request: ProposalRequest):
        ctx = request.context
        frame = ctx.observation
        return await provider.decide(
            ctx.model,
            ctx.instruction,
            frame.png,
            frame.bounds.width,
            frame.bounds.height,
            history=request.history,
        )

    async def verify(self, provider, request: VerificationRequest):
        ctx = request.context
        frame = ctx.observation
        return await provider.verify_action(
            ctx.model,
            ctx.instruction,
            request.action.to_action(),
            request.expected,
            frame.png,
            frame.bounds.width,
            frame.bounds.height,
            request.before_image,
        )

    async def compare(self, before, after, action, *, diagnostics=None):
        # Runs on the same ProviderJobs thread as before; no nested thread pool.
        return scene_matches(before, after, action, diagnostics=diagnostics)

    def prepare(
        self,
        decision,
        action,
        observation,
        instruction,
        *,
        single_click_policy,
        single_click_dispatched,
        close_dispatched,
    ):
        # These are host task facts, not model-selected permissions. Reuse the
        # existing policy exactly; admission and fresh dispatch checks stay outside.
        validate_proposal_intent(decision, instruction)
        if single_click_policy:
            if single_click_dispatched:
                raise ValueError(
                    "The requested one-click input was already dispatched; no further input."
                )
            if action.get("op") not in {"move", "click"}:
                raise ValueError(
                    "A one-click request permits pointer movement and one click only; no substitutes."
                )
            if action.get("op") == "click" and action.get("count", 1) != 1:
                raise ValueError("The request permits one click only, not a double click.")
        closing = window_close_requested(instruction)
        if closing and (close_dispatched or action != {"op": "chord", "keys": ["ALT", "F4"]}):
            raise ValueError(
                "Current-window closing supports one reviewed Alt+F4 only; no guessed clicks or retry."
            )
        return prepare_action(
            action,
            observation,
            allow_window_close=closing,
            allow_terminal_window=terminal_window_requested(instruction),
        )

    def ground(
        self, proposal, observation, physical_action, *, generation, frame_ref=None, effect_ref=None
    ):
        return grounded_from_checked(
            proposal,
            observation,
            physical_action,
            generation=generation,
            frame_ref=frame_ref,
            effect_ref=effect_ref,
        )

    def assess(
        self,
        grounded,
        result,
        observation,
        *,
        generation,
        model,
        expected_result,
        evidence_ref,
        frame_ref=None,
    ):
        return verification_from_legacy(
            grounded,
            result,
            observation,
            generation=generation,
            model=model,
            expected_result=expected_result,
            evidence_ref=evidence_ref,
            frame_ref=frame_ref,
        )
