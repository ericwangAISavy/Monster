"""Observation binding and conservative supervised-action policy."""

import re

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage

from monster.computer.contracts import Bounds, validate_action


def approval_reason(decision):
    """User-authorized automatic mode still escalates sensitive/uncertain effects.

    Model classification is fallible. Deterministic escalation and blocked-action
    validation remain in effect; this is not a security boundary for arbitrary apps.
    """
    action = decision.action or {}
    if re.search(
        r"(?i)\b(send(?:ing)?|submit(?:ting)?|delet(?:e|ing|ion)|remov(?:e|ing)|"
        r"purchas(?:e|ing)|buy(?:ing)?|pay(?:ment|ments|ing)?|transfer(?:ring)?|install(?:ing|ation)?|"
        r"password|credential(?:s)?|shar(?:e|ing)|publish(?:ing)?|overwrit(?:e|ing)|save)\b",
        decision.summary,
    ):
        return "Potentially sensitive action detected."
    if decision.risk == "sensitive":
        return "The model marked this action sensitive."
    if decision.risk == "uncertain":
        return "The model is uncertain about this action's target or effect."
    if action.get("op") in {"move", "scroll", "text"}:
        return None
    if action.get("op") == "click":
        return (
            None if decision.risk == "routine" else "Click target/effect has not been classified."
        )
    if action.get("op") == "press":
        if action.get("key") in {
            "LEFT",
            "RIGHT",
            "UP",
            "DOWN",
            "HOME",
            "END",
            "PAGEUP",
            "PAGEDOWN",
            "TAB",
            "BACKSPACE",
        }:
            return None
        if action.get("key") in {"ENTER", "SPACE", "WIN"} and decision.risk == "routine":
            return None
        return "This key's effect needs review."
    if action.get("op") == "chord" and action.get("keys") in (
        ["CTRL", "A"],
        ["CTRL", "C"],
        ["CTRL", "HOME"],
        ["CTRL", "END"],
        ["SHIFT", "LEFT"],
        ["SHIFT", "RIGHT"],
        ["SHIFT", "ENTER"],
    ):
        return None
    return "This operation needs review."


def needs_human_approval(decision):
    return approval_reason(decision) is not None


def prepare_action(action, observation):
    action = validate_action(
        action, [Bounds(0, 0, observation.bounds.width, observation.bounds.height)]
    )
    if action.get("op") in {"text", "press", "chord"} and not observation.foreground:
        raise ValueError("Keyboard actions require a captured foreground window.")
    if action.get("op") == "text":
        text = action.get("text", "")
        if re.search(
            r"(?i)(powershell|\bcmd\b|\bpwsh\b|javascript:|data:|https?://|\.exe\b|\brm\s+-|\bsudo\b)",
            text,
        ):
            raise ValueError(
                "Commands, executables and URLs are outside this supervised text demo."
            )
    if action.get("op") == "press" and action.get("key") == "DELETE":
        raise ValueError("Delete is outside the supervised demo.")
    if action.get("op") == "chord":
        keys = action.get("keys", [])
        if keys not in (
            ["CTRL", "A"],
            ["CTRL", "C"],
            ["CTRL", "Z"],
            ["CTRL", "HOME"],
            ["CTRL", "END"],
            ["SHIFT", "LEFT"],
            ["SHIFT", "RIGHT"],
            ["SHIFT", "ENTER"],
        ):
            raise ValueError("Shortcut is outside the supervised demo allowlist.")
    if "x" in action:
        action["x"] += observation.bounds.x
        action["y"] += observation.bounds.y
    return validate_action(action, list(observation.displays))


def scene_matches(before, after, action):
    if (
        before.bounds != after.bounds
        or before.displays != after.displays
        or not before.foreground
        or before.foreground != after.foreground
    ):
        return False
    old, new = QImage.fromData(before.png), QImage.fromData(after.png)
    if old.isNull() or new.isNull() or old.size() != new.size():
        return False
    x, y = action.get("x"), action.get("y")
    if x is not None:
        region = QRect(x - 20, y - 20, 41, 41).intersected(old.rect())
        if old.copy(region) != new.copy(region):
            return False
    old = old.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio).convertToFormat(
        QImage.Format.Format_RGBA8888
    )
    new = new.scaled(old.size()).convertToFormat(QImage.Format.Format_RGBA8888)
    a, b = bytes(old.constBits()), bytes(new.constBits())
    changed = 0
    pixels = old.width() * old.height()
    for index in range(pixels):
        offset = index * 4
        if max(abs(a[offset + c] - b[offset + c]) for c in range(3)) > 20:
            changed += 1
            if changed > max(1, pixels * 0.002):
                return False
    return True
