"""Bounded in-memory action feedback; screenshot hashes do not decide progress."""

import hashlib
import json
import re
import unicodedata


def canonical_action(action):
    value = dict(action)
    if value.get("op") == "click":
        value.setdefault("button", "left")
        value.setdefault("count", 1)
    if value.get("op") == "scroll":
        value.setdefault("horizontal", False)
    if value.get("op") == "text":
        value["text"] = unicodedata.normalize("NFC", value["text"]).strip()
    return value


def action_signature(action):
    value = canonical_action(action)
    # Small coordinate jitter must not turn a failed click into a new strategy.
    for key in ("x", "y", "end_x", "end_y"):
        if key in value:
            value[key] = round(value[key] / 24)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def draft_only(instruction):
    return bool(
        re.search(r"(?i)\b(type|write|draft|prepare)\b", instruction)
        and re.search(r"(?i)\b(slack|chat|message|msg|reply|email)\b", instruction)
    )


def validate_draft_action(decision, instruction, text_inserted=False):
    if not draft_only(instruction):
        return
    action = decision.action or {}
    if text_inserted:
        raise ValueError(
            "Draft text was already inserted. No further input is allowed; verify the unsent composer."
        )
    if action.get("op") in {"text", "move", "scroll"} or action.get("keys") == ["SHIFT", "ENTER"]:
        return
    summary = decision.summary.casefold()
    for phrase in ("do not send", "don't send", "not send", "without sending"):
        summary = summary.replace(phrase, "")
    if (
        action.get("op") == "press" and action.get("key") in {"ENTER", "SPACE", "WIN"}
    ) or re.search(r"\b(send|submit|post|publish)(?:ing)?\b", summary):
        raise ValueError(
            "Draft-only task: sending/Enter is blocked. Leave the text in the composer."
        )


def equivalent_action(left, right):
    a, b = canonical_action(left), canonical_action(right)
    if a.get("op") != b.get("op"):
        return False
    if (
        "x" in a
        and "x" in b
        and (abs(a.pop("x") - b.pop("x")) > 24 or abs(a.pop("y") - b.pop("y")) > 24)
    ):
        return False
    return a == b


def history_window(items, limit=9000):
    selected = []
    size = 0
    for item in reversed(items):
        if size + len(item) > limit:
            break
        selected.append(item)
        size += len(item)
    return tuple(reversed(selected))


def action_description(action):
    """Human-shareable metadata: no typed text, prompts, or verification evidence."""
    if action.get("op") == "text":
        return f"text ({len(action.get('text', ''))} characters)"
    if "x" in action:
        return f"{action['op']} at image ({action['x']}, {action['y']})"
    return action.get("op", "unknown")
