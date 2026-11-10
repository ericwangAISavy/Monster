"""Strict, bounded local benchmark inputs; labels never enter model requests."""

import asyncio
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from monster.agent.contracts import ActionPayload, Hash, Reference
from monster.benchmarks.grounding import validate_oracle
from monster.providers.ollama import strict_json
from monster.providers.ui_venus_preview import _read, load_observation


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ImageSpec(Spec):
    path: str = Field(min_length=1, max_length=256)
    sha256: Hash
    origin_px: tuple[int, int] = (0, 0)
    dpi_percent: Literal[100, 125, 150, 200] = 100

    @field_validator("origin_px", mode="before")
    @classmethod
    def json_origin(cls, value, info: ValidationInfo):
        return tuple(value) if info.mode == "json" and type(value) is list else value


class Case(Spec):
    id: Reference
    scenario: Reference
    operation: Literal["grounding", "action_verification", "completion"]
    instruction: str = Field(min_length=1, max_length=2000, repr=False)
    image: ImageSpec
    before: ImageSpec | None = None
    action: dict | None = Field(default=None, repr=False)
    expected: str | None = Field(default=None, max_length=1000, repr=False)
    oracle: dict | None = Field(default=None, repr=False)
    truth: Literal["succeeded", "failed", "uncertain"] | None = None
    recorded_response: str | None = Field(default=None, max_length=64000, repr=False)

    @model_validator(mode="after")
    def scope(self):
        if self.operation == "grounding":
            if self.oracle is None or any(
                v is not None for v in (self.truth, self.action, self.expected, self.before)
            ):
                raise ValueError("Invalid grounding case")
        elif self.truth is None or self.oracle is not None:
            raise ValueError("Verification requires an independent label")
        elif self.operation == "action_verification":
            if self.action is None or not self.expected:
                raise ValueError("Missing action/criterion")
            ActionPayload.from_action(self.action)
        elif any(v is not None for v in (self.action, self.expected, self.before)):
            raise ValueError("Completion uses only the task and image")
        return self


class Manifest(Spec):
    version: Literal[1] = 1
    split: Literal["synthetic", "held_out"]
    label_source: Literal["application_owned_synthetic", "declared_human_review"]
    cases: tuple[Case, ...] = Field(min_length=1, max_length=128)

    @field_validator("cases", mode="before")
    @classmethod
    def json_cases(cls, value, info: ValidationInfo):
        return tuple(value) if info.mode == "json" and type(value) is list else value

    @model_validator(mode="before")
    @classmethod
    def version_type(cls, value):
        if type(value) is dict and type(value.get("version")) is not int:
            raise ValueError("Unsupported manifest version")
        return value

    @model_validator(mode="after")
    def ids(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case")
        if (self.split == "synthetic") != (self.label_source == "application_owned_synthetic"):
            raise ValueError("Inconsistent fixture provenance")
        return self


def read_image(directory: Path, spec: ImageSpec, frame_ref):
    relative = Path(spec.path)
    resolved = (directory / relative).resolve()
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not resolved.is_relative_to(directory.resolve())
    ):
        raise ValueError("Fixture image path outside dataset")
    obs = load_observation(resolved, spec.origin_px, frame_ref)
    if obs.sha256 != spec.sha256:
        raise ValueError("Fixture image changed")
    from PySide6.QtGui import QImage

    decoded = QImage.fromData(obs.png)
    if decoded.isNull() or (decoded.width(), decoded.height()) != (
        obs.bounds.width,
        obs.bounds.height,
    ):
        raise ValueError("Invalid fixture PNG")
    return obs


async def read_manifest(path):
    path = Path(path)
    raw = _read(path, 1024 * 1024)
    value = strict_json(raw)
    # Re-encode strict JSON to retain strict JSON tuple semantics and reject duplicates/NaN.
    import json

    manifest = Manifest.model_validate_json(json.dumps(value, allow_nan=False))
    for case in manifest.cases:
        await asyncio.sleep(0)
        obs = read_image(path.parent, case.image, case.id)
        if case.before:
            before = read_image(path.parent, case.before, case.id + "-before")
            if before.bounds != obs.bounds or case.before.dpi_percent != case.image.dpi_percent:
                raise ValueError("Mismatched before/after fixture geometry")
        if case.oracle:
            validate_oracle(case.oracle, obs.bounds.width, obs.bounds.height)
    return manifest, hashlib.sha256(raw).hexdigest()
