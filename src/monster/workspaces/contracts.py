"""Strict, reference-only workspace protocol. IDs never contain addresses/secrets."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, StringConstraints, model_validator

from monster.mission.contracts import Contract

OpaqueId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}$")]


def new_id():
    return uuid4().hex


class WorkspaceDescriptor(Contract):
    workspace_id: OpaqueId
    kind: Literal["windows_guest"] = "windows_guest"
    display_name: Label
    backend: Literal["manual_vm"] = "manual_vm"
    qualification: Literal["pending"] = "pending"


class SessionBinding(Contract):
    workspace_id: OpaqueId
    guest_instance_id: OpaqueId
    generation: int = Field(ge=1, le=2**31)
    session_ref: OpaqueId
    display_digest: Digest
    provider_digest: Digest


class GuestCapabilities(Contract):
    protocol_version: Literal[1] = 1
    binding: SessionBinding
    provenance: Literal["simulated"] = "simulated"
    capture_available: bool = False
    input_worker_ready: Literal[False] = False
    emergency_stop_ready: Literal[False] = False
    native_qualification: Literal["pending"] = "pending"
    supported_operations: tuple[
        Literal["status", "start", "approve", "pause", "stop", "resume", "reconcile", "preview"],
        ...,
    ] = ("status", "start", "approve", "pause", "stop", "resume", "reconcile", "preview")


class GuestStatus(Contract):
    binding: SessionBinding
    state: Literal[
        "idle",
        "waiting_approval",
        "running",
        "paused",
        "completed",
        "partially_complete",
        "cancelled",
        "quarantined",
        "offline",
    ]
    reason: Literal[
        "ready",
        "consent_required",
        "verified",
        "stopped",
        "link_lost",
        "session_changed",
        "display_changed",
        "provider_changed",
        "worker_failed",
        "clock_changed",
        "restored",
        "reconciliation_required",
        "unavailable",
        "budget",
        "expired",
        "denied",
    ] = "ready"
    mission_id: OpaqueId | None = None
    verified_stages: int = Field(default=0, ge=0, le=6)
    total_stages: Literal[6] = 6
    cleanup: Literal["confirmed", "active", "unknown"] = "confirmed"
    unresolved: bool = False
    provenance: Literal["simulated"] = "simulated"
    input_charged: int = Field(default=0, ge=0, le=10000)
    models_charged: int = Field(default=0, ge=0, le=10000)


class ApprovalChallenge(Contract):
    challenge_id: OpaqueId
    binding: SessionBinding
    mission_id: OpaqueId
    effect_id: Digest
    fingerprint: Digest
    observation_id: OpaqueId
    scene_digest: Digest
    kind: Literal["mission_consent"] = "mission_consent"
    target: Literal["disposable-editor"] = "disposable-editor"
    risk: Literal["local_reversible"] = "local_reversible"
    summary: Literal[
        "Six reviewed synthetic editor stages; no native input or document creation."
    ] = "Six reviewed synthetic editor stages; no native input or document creation."
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    final_confirmation_required: Literal[True] = True


class DisplayRect(Contract):
    x: int = Field(ge=-32768, le=32768)
    y: int = Field(ge=-32768, le=32768)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)


class PreviewFrame(Contract):
    binding: SessionBinding
    frame_id: OpaqueId
    captured_at: float = Field(gt=0, allow_inf_nan=False)
    source_bounds: DisplayRect
    width: int = Field(ge=1, le=1024)
    height: int = Field(ge=1, le=1024)
    png_base64: str = Field(min_length=1, max_length=32768, repr=False)
    provenance: Literal["synthetic"] = "synthetic"
    use_for_input: Literal[False] = False

    @model_validator(mode="after")
    def safe_image(self):
        import base64
        import struct

        try:
            raw = base64.b64decode(self.png_base64, validate=True)
            if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 33:
                raise ValueError("invalid_preview")
            if struct.unpack(">II", raw[16:24]) != (self.width, self.height):
                raise ValueError("invalid_preview_geometry")
        except (ValueError, TypeError):
            raise ValueError("invalid_preview") from None
        if self.width * self.height > 1024 * 1024:
            raise ValueError("preview_too_large")
        return self

    def source_point(self, x, y):
        if (
            type(x) is not int
            or type(y) is not int
            or not (0 <= x < self.width and 0 <= y < self.height)
        ):
            raise ValueError("preview_point_outside_bounds")
        b = self.source_bounds
        return (
            b.x + round(x * (b.width - 1) / max(1, self.width - 1)),
            b.y + round(y * (b.height - 1) / max(1, self.height - 1)),
        )


class Hello(Contract):
    op: Literal["HELLO"] = "HELLO"


class Heartbeat(Contract):
    op: Literal["HEARTBEAT"] = "HEARTBEAT"


class StatusRequest(Contract):
    op: Literal["STATUS"] = "STATUS"


class StartMission(Contract):
    op: Literal["START_MISSION"] = "START_MISSION"
    mode: Literal["guest_simulation"] = "guest_simulation"
    template_ref: Literal["editor-six-v1"] = "editor-six-v1"


class ResumeMission(Contract):
    op: Literal["RESUME_MISSION"] = "RESUME_MISSION"
    mission_id: OpaqueId


class ApprovalResponse(Contract):
    op: Literal["APPROVAL_RESPONSE"] = "APPROVAL_RESPONSE"
    challenge_id: OpaqueId
    mission_id: OpaqueId
    fingerprint: Digest
    observation_id: OpaqueId
    decision: Literal["approve", "deny"]


class Pause(Contract):
    op: Literal["PAUSE_AFTER_STAGE"] = "PAUSE_AFTER_STAGE"


class Stop(Contract):
    op: Literal["STOP_IMMEDIATE"] = "STOP_IMMEDIATE"


class Reconcile(Contract):
    op: Literal["RECONCILE_REQUEST"] = "RECONCILE_REQUEST"
    mission_id: OpaqueId


class PreviewRequest(Contract):
    op: Literal["PREVIEW_REQUEST"] = "PREVIEW_REQUEST"
    consent: Literal[True]


Command = Annotated[
    Hello
    | Heartbeat
    | StatusRequest
    | StartMission
    | ResumeMission
    | ApprovalResponse
    | Pause
    | Stop
    | Reconcile
    | PreviewRequest,
    Field(discriminator="op"),
]


class Envelope(Contract):
    protocol_version: Literal[1] = 1
    request_id: OpaqueId
    binding: SessionBinding
    sequence: int = Field(ge=1, le=2**31)
    issued_at: float = Field(gt=0, allow_inf_nan=False)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    command: Command

    @model_validator(mode="after")
    def short_lived(self):
        if not 0 < self.expires_at - self.issued_at <= 10:
            raise ValueError("invalid_request_lifetime")
        return self


class Reply(Contract):
    request_id: OpaqueId
    binding: SessionBinding
    event_sequence: int = Field(ge=1, le=2**31)
    result: Literal[
        "status",
        "capabilities",
        "challenge",
        "accepted",
        "stop_ack",
        "preview",
        "reconciliation",
        "error",
    ]
    status: GuestStatus
    capabilities: GuestCapabilities | None = None
    challenge: ApprovalChallenge | None = None
    preview: PreviewFrame | None = None
    error: Literal[
        "none",
        "invalid_request",
        "unavailable",
        "stale_binding",
        "unpaired",
        "replayed",
        "busy",
        "qualification_required",
        "reconciliation_required",
        "preview_unavailable",
        "journal_unavailable",
    ] = "none"

    @model_validator(mode="after")
    def nested_binding(self):
        for value in (self.status, self.capabilities, self.challenge, self.preview):
            if value is not None and value.binding != self.binding:
                raise ValueError("cross_workspace_reply")
        if self.challenge and self.challenge.mission_id != self.status.mission_id:
            raise ValueError("cross_mission_challenge")
        return self


class SignedFrame(Contract):
    grant_id: OpaqueId
    payload: str = Field(min_length=1, max_length=49152, repr=False)
    mac: Digest = Field(repr=False)
