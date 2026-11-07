"""Provider-neutral contracts for image-based, preview-only decisions."""

from dataclasses import dataclass
from typing import Protocol


class ProviderError(RuntimeError):
    """Safe user-facing category/message, never a raw HTTP body or model payload."""


@dataclass(frozen=True)
class Model:
    id: str
    digest: str = ""


@dataclass(frozen=True)
class Capabilities:
    vision: bool | None
    local: bool | None
    context_length: int | None = None


@dataclass(frozen=True)
class ProbeResult:
    model: str
    digest: str
    server_version: str
    vision_verified: bool
    structured_verified: bool


@dataclass(frozen=True)
class Decision:
    kind: str
    summary: str
    action: dict | None = None
    risk: str | None = None
    expected_result: str | None = None
    coordinate_source: dict | None = None


@dataclass(frozen=True)
class Verification:
    status: str
    evidence: str


class Provider(Protocol):
    async def health(self) -> str: ...
    async def models(self) -> list[Model]: ...
    async def capabilities(self, model: str) -> Capabilities: ...
    async def probe(self, model: str) -> ProbeResult: ...
    async def verify_action(
        self,
        model: str,
        instruction: str,
        action: dict,
        expected: str,
        image: bytes,
        width: int,
        height: int,
    ) -> Verification: ...
    async def decide(
        self,
        model: str,
        instruction: str,
        image: bytes,
        width: int,
        height: int,
        history: tuple[str, ...] = (),
    ) -> Decision: ...
