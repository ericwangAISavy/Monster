"""Explicit fake-console pairing. Credentials exist only in RAM and never in JSON.

No live pairing or TOFU is provided. Native OS credential integration awaits the
reviewed TLS design and Windows qualification; simulation cannot enable it.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from .contracts import new_id
from .protocol import LinkError


@dataclass
class PairingGrant:
    grant_id: str
    workspace_id: str
    guest_instance_id: str
    host_id: str
    host_fingerprint: str
    guest_fingerprint: str
    expires_at: float
    key: bytes = field(repr=False)
    revoked: bool = False


@dataclass(frozen=True)
class PairingOffer:
    offer_id: str
    host_id: str
    host_fingerprint: str
    guest_fingerprint: str
    expires_at: float
    code: str = field(repr=False)


class SimulatedPairing:
    def __init__(self, binding, *, clock):
        self.binding, self.clock = binding, clock
        self.host_id = new_id()
        self.host_fingerprint = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        self.guest_fingerprint = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        self.offer = None
        self.grant = None

    def begin(self):
        if self.grant and not self.grant.revoked and self.grant.expires_at > self.clock():
            raise LinkError("single_controller_only")
        self.offer = PairingOffer(
            new_id(),
            self.host_id,
            self.host_fingerprint,
            self.guest_fingerprint,
            self.clock() + 60,
            f"{secrets.randbelow(10**8):08d}",
        )
        return self.offer

    def confirm(self, offer_id, *, code, host_fingerprint, guest_fingerprint, human_verified):
        offer = self.offer
        if (
            offer is None
            or offer.offer_id != offer_id
            or self.clock() >= offer.expires_at
            or human_verified is not True
            or not hmac.compare_digest(offer.code, code)
            or host_fingerprint != offer.host_fingerprint
            or guest_fingerprint != offer.guest_fingerprint
        ):
            raise LinkError("pairing_verification_failed")
        self.offer = None  # one use; second confirmation cannot create another controller
        self.grant = PairingGrant(
            new_id(),
            self.binding.workspace_id,
            self.binding.guest_instance_id,
            offer.host_id,
            host_fingerprint,
            guest_fingerprint,
            self.clock() + 3600,
            secrets.token_bytes(32),
        )
        return self.grant

    def revoke(self):
        self.offer = None
        if self.grant:
            self.grant.revoked = True

    def rotate(self, binding):
        self.revoke()
        self.binding = binding
        self.guest_fingerprint = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
