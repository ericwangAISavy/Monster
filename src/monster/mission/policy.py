"""Deterministic authority. Model/page strings cannot create permissions."""

from dataclasses import dataclass
from enum import StrEnum

POLICY_VERSION = "mission-effects-v1"


class Risk(StrEnum):
    OBSERVE = "observe_only"
    REVERSIBLE = "local_reversible"
    PERSISTENT = "local_persistent"
    EXTERNAL = "external_commit"
    DESTRUCTIVE = "destructive"
    CREDENTIAL = "credential_or_identity"
    FINANCIAL = "financial_or_legal"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PolicyDecision:
    risk: str
    decision: str
    reason: str
    policy_version: str = POLICY_VERSION


def evaluate(risk: str, *, active: bool, live=True, scoped_low_risk=False) -> PolicyDecision:
    # No caller/model flag enables scoped autonomy in this release.
    if (
        type(active) is not bool
        or type(live) is not bool
        or type(scoped_low_risk) is not bool
        or not active
        or risk not in set(Risk)
        or scoped_low_risk
    ):
        return PolicyDecision("unknown", "deny", "invalid_scope")
    if risk == "credential_or_identity":
        return PolicyDecision(risk, "handoff", "user_authentication")
    if risk in ("unknown", "destructive", "financial_or_legal") or (
        live and risk in ("external_commit", "local_persistent")
    ):
        return PolicyDecision(risk, "deny", "outside_phase3_scope")
    return PolicyDecision(
        risk, "allow" if risk == "observe_only" else "review", "exact_effect_review"
    )


def classify(action: dict, *, expected_text: str, model_risk=None) -> str:
    # A model may escalate uncertainty; benign/low never lowers deterministic risk.
    if model_risk not in (None, "benign", "low"):
        return "unknown"
    if type(action) is not dict:
        return "unknown"
    if set(action) == {"op", "text"} and action["op"] == "text" and action["text"] == expected_text:
        return "local_reversible"
    if (
        set(action) == {"op", "key"}
        and action["op"] == "press"
        and action["key"] in ("LEFT", "RIGHT", "HOME", "END")
    ):
        return "local_reversible"
    if (
        set(action) == {"op", "x", "y"}
        and action["op"] == "move"
        and all(type(action[k]) is int for k in ("x", "y"))
    ):
        return "local_reversible"
    # Coordinates/button labels alone cannot establish a harmless click. Enter,
    # hotkeys, arbitrary text, clipboard, terminals and all other ops fail closed.
    return "unknown"
