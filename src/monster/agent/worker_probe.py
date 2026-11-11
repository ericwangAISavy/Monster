"""No-frame, read-only compatibility packets; never native qualification or input grants."""

from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from monster.agent.contracts import Hash, Record, Reference, model_reference
from monster.mission.scheduling import canonical_digest
from monster.providers.base import ProbeResult
from monster.providers.config import OllamaConfig


class ProbeBinding(Record):
    request_ref: Reference
    task_ref: Reference
    controller_generation: int = Field(ge=0)
    operation: Literal["compatibility_probe"] = "compatibility_probe"
    model_ref: Reference
    configuration_digest: Hash


@dataclass(frozen=True)
class ProbeRequest:
    config: OllamaConfig = field(repr=False)
    binding: ProbeBinding

    def __post_init__(self):
        if type(self.config) is not OllamaConfig or not isinstance(self.binding, ProbeBinding):
            raise TypeError("Frozen probe config/binding required")
        self.config.validate()
        if not self.config.model.strip():
            raise ValueError("Selected probe model required")
        binding = ProbeBinding.model_validate(self.binding)
        if binding.model_ref != model_reference(
            self.config.model
        ) or binding.configuration_digest != canonical_digest(asdict(self.config)):
            raise ValueError("Changed probe context")

    @classmethod
    def create(cls, config, *, generation, task_ref):
        return cls(
            config,
            ProbeBinding(
                request_ref=uuid4().hex,
                task_ref=task_ref,
                controller_generation=generation,
                model_ref=model_reference(config.model),
                configuration_digest=canonical_digest(asdict(config)),
            ),
        )


class CompatibilityResult(Record):
    binding: ProbeBinding
    model_digest: str = Field(min_length=1, max_length=256)
    server_version: str = Field(min_length=1, max_length=128)
    vision_verified: Literal[True]
    structured_verified: Literal[True]
    evidence_kind: Literal["synthetic_compatibility_check"] = "synthetic_compatibility_check"

    @field_validator("vision_verified", "structured_verified", mode="before")
    @classmethod
    def positive_boolean(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Positive boolean compatibility evidence required")
        return value

    @field_validator("model_digest", "server_version")
    @classmethod
    def bounded_metadata(cls, value):
        if not value.strip() or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Invalid probe metadata")
        return value

    @classmethod
    def from_probe(cls, request, result):
        if not isinstance(result, ProbeResult) or result.model != request.config.model:
            raise ValueError("Wrong probe result/model")
        return cls(
            binding=request.binding,
            model_digest=result.digest,
            server_version=result.server_version,
            vision_verified=result.vision_verified,
            structured_verified=result.structured_verified,
        )

    def to_probe_result(self, model):
        if model_reference(model) != self.binding.model_ref:
            raise ValueError("Probe model changed")
        return ProbeResult(
            model,
            self.model_digest,
            self.server_version,
            self.vision_verified,
            self.structured_verified,
        )
