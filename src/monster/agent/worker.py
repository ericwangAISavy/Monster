"""Bounded, read-only operational work; never an execution authority.

Each invocation does one inference, comparison or projection. ProviderJobs owns
threading/cancellation. The host owns captures, budgets, reviews, intent, input,
result interpretation and cleanup. No worker loop, journal or executor lives here.
Private images/context/payloads are RAM-only and excluded from packet reprs.
"""

import hashlib
from dataclasses import dataclass, field
from uuid import uuid4

from monster.agent.contract_adapters import (
    grounded_from_checked,
    observation_frame,
    verification_from_legacy,
)
from monster.agent.contracts import ActionPayload, model_reference
from monster.agent.keyboard import terminal_window_requested
from monster.agent.validation import (
    prepare_action,
    scene_matches,
    validate_proposal_intent,
    window_close_requested,
)
from monster.agent.worker_checks import VisualCheckRequest, VisualCheckResult
from monster.agent.worker_probe import CompatibilityResult, ProbeRequest
from monster.agent.worker_results import (
    ActionVerificationResult,
    ProposalResult,
    SceneResult,
    WorkerBinding,
)
from monster.computer.screenshot import Observation
from monster.providers.base import ProviderError


@dataclass(frozen=True)
class VisualContext:
    model: str = field(repr=False)
    instruction: str = field(repr=False)
    observation: Observation = field(repr=False)


def _binding(operation, context, generation, task_ref, *, before=None, reference=None):
    return WorkerBinding(
        request_ref=uuid4().hex,
        task_ref=task_ref,
        operation=operation,
        model_ref=model_reference(context.model),
        frame=observation_frame(context.observation, generation),
        before_frame=observation_frame(before, generation) if before else None,
        reference_image_sha256=hashlib.sha256(reference).hexdigest()
        if reference is not None
        else None,
    )


def _validate_request(binding, context, operation, *, before=None, reference=None):
    if not isinstance(binding, WorkerBinding) or not isinstance(context, VisualContext):
        raise TypeError("Immutable typed Worker binding/context required")
    binding = WorkerBinding.model_validate(binding)
    expected = _binding(
        operation,
        context,
        binding.frame.controller_generation,
        binding.task_ref,
        before=before,
        reference=reference,
    ).model_copy(update={"request_ref": binding.request_ref})
    if binding != expected:
        raise ValueError("Changed Worker request context")


@dataclass(frozen=True)
class ProposalRequest:
    context: VisualContext = field(repr=False)
    binding: WorkerBinding
    history: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        _validate_request(self.binding, self.context, "proposal")
        if type(self.history) is not tuple or any(type(item) is not str for item in self.history):
            raise ValueError("Worker history must be an immutable snapshot")

    @classmethod
    def create(cls, context, *, generation, task_ref, history=()):
        return cls(context, _binding("proposal", context, generation, task_ref), history)


@dataclass(frozen=True)
class VerificationRequest:
    context: VisualContext = field(repr=False)
    action: ActionPayload = field(repr=False)
    expected: str = field(repr=False)
    binding: WorkerBinding
    before_image: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        _validate_request(
            self.binding, self.context, "action_verification", reference=self.before_image
        )
        if not isinstance(self.action, ActionPayload):
            raise TypeError("Immutable action required")
        ActionPayload.model_validate(self.action)
        if type(self.expected) is not str or not self.expected.strip():
            raise ValueError("Missing criterion")
        if self.before_image is not None and type(self.before_image) is not bytes:
            raise TypeError("Immutable reference image required")

    @classmethod
    def from_action(cls, context, action, expected, *, generation, task_ref, before_image=None):
        return cls(
            context,
            ActionPayload.from_action(action),
            expected,
            _binding("action_verification", context, generation, task_ref, reference=before_image),
            before_image,
        )


@dataclass(frozen=True)
class SceneRequest:
    context: VisualContext = field(repr=False)
    before: Observation = field(repr=False)
    action: ActionPayload = field(repr=False)
    binding: WorkerBinding

    def __post_init__(self):
        _validate_request(self.binding, self.context, "scene_comparison", before=self.before)
        if not isinstance(self.action, ActionPayload):
            raise TypeError("Immutable action required")
        ActionPayload.model_validate(self.action)

    @classmethod
    def create(cls, context, before, action, *, generation, task_ref):
        return cls(
            context,
            before,
            ActionPayload.from_action(action),
            _binding("scene_comparison", context, generation, task_ref, before=before),
        )


class BoundedWorker:
    """Stateless operations only. Returned proposals/assessments grant no input."""

    async def probe(self, provider, request: ProbeRequest):
        result = await provider.probe(request.config.model)
        try:
            return CompatibilityResult.from_probe(request, result)
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("Model returned an invalid Worker compatibility result.") from None

    async def propose(self, provider, request: ProposalRequest):
        ctx = request.context
        frame = ctx.observation
        result = await provider.decide(
            ctx.model,
            ctx.instruction,
            frame.png,
            frame.bounds.width,
            frame.bounds.height,
            history=request.history,
        )
        try:
            return ProposalResult.from_decision(request.binding, result)
        except (ValueError, TypeError, RecursionError):
            raise ProviderError("Model returned an invalid Worker proposal snapshot.") from None

    async def verify(self, provider, request: VerificationRequest):
        ctx = request.context
        frame = ctx.observation
        result = await provider.verify_action(
            ctx.model,
            ctx.instruction,
            request.action.to_action(),
            request.expected,
            frame.png,
            frame.bounds.width,
            frame.bounds.height,
            request.before_image,
        )
        try:
            return ActionVerificationResult(
                binding=request.binding, status=result.status, evidence=result.evidence
            )
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("Model returned an invalid Worker action assessment.") from None

    async def check(self, provider, request: VisualCheckRequest):
        ctx = request.context
        frame = ctx.observation
        # Explicit read-only operations, never model-selected method dispatch.
        operation = request.binding.operation
        if operation == "draft_target":
            result = await provider.verify_draft_target(
                ctx.model,
                ctx.instruction,
                request.action.to_action(),
                frame.png,
                frame.bounds.width,
                frame.bounds.height,
            )
        elif operation == "drawing_target":
            result = await provider.verify_drawing_target(
                ctx.model,
                ctx.instruction,
                request.action.to_action(),
                frame.png,
                frame.bounds.width,
                frame.bounds.height,
            )
        elif operation == "app_launch":
            result = await provider.verify_action(
                ctx.model,
                ctx.instruction,
                {"op": "screenshot"},
                request.expected,
                frame.png,
                frame.bounds.width,
                frame.bounds.height,
            )
        elif operation == "completion":
            result = await provider.verify_completion(
                ctx.model, ctx.instruction, frame.png, frame.bounds.width, frame.bounds.height
            )
        else:
            raise ProviderError("Unsupported read-only Worker operation.")
        try:
            return VisualCheckResult(
                binding=request.binding, status=result.status, evidence=result.evidence
            )
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("Model returned an invalid read-only visual assessment.") from None

    async def compare(self, request: SceneRequest):
        details = {}
        matched = scene_matches(
            request.before,
            request.context.observation,
            request.action.to_action(),
            diagnostics=details,
        )
        if "patch" in details:
            details["patch"] = tuple(details["patch"])
        try:
            return SceneResult(binding=request.binding, **{**details, "matched": matched})
        except (ValueError, TypeError):
            raise ProviderError(
                "Screen comparison returned an invalid Worker assessment."
            ) from None

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
