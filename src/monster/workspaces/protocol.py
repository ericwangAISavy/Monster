"""Bounded parser and RAM HMAC for simulation. This is NOT a network/TLS transport."""

import hashlib
import hmac

from monster.mission.strategic.contracts import StrategicLimits
from monster.mission.strategic.planning import PlannerFailure, parse_response

from .contracts import SignedFrame

MAX_FRAME_BYTES = 65536


class LinkError(ValueError):
    """Fixed, non-content-bearing error category."""


def parse(raw, model):
    try:
        return parse_response(raw, model, StrategicLimits(max_response_bytes=MAX_FRAME_BYTES))
    except (PlannerFailure, ValueError, TypeError, UnicodeError):
        raise LinkError("invalid_frame") from None


def seal(value, grant):
    payload = value.model_dump_json()
    mac = hmac.new(grant.key, (grant.grant_id + payload).encode(), hashlib.sha256).hexdigest()
    result = (
        SignedFrame(grant_id=grant.grant_id, payload=payload, mac=mac).model_dump_json().encode()
    )
    if len(result) > MAX_FRAME_BYTES:
        raise LinkError("frame_too_large")
    return result


def open_frame(raw, grant, model, now):
    # Grant validity precedes private message parsing or handler dispatch.
    if grant is None or grant.revoked or now >= grant.expires_at:
        raise LinkError("unpaired")
    frame = parse(raw, SignedFrame)
    expected = hmac.new(
        grant.key, (grant.grant_id + frame.payload).encode(), hashlib.sha256
    ).hexdigest()
    if frame.grant_id != grant.grant_id or not hmac.compare_digest(expected, frame.mac):
        raise LinkError("unauthenticated")
    return parse(frame.payload, model)
