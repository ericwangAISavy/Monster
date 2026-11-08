"""Observation binding and conservative supervised-action policy."""

import re

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage

from monster.agent.keyboard import (
    NEW_TERMINAL_WINDOW,
    ROUTINE_CHARACTER_KEYS,
    terminal_window_requested,
    validate_quoted_typing,
    validate_shortcut_description,
)
from monster.agent.progress import drawing_requested
from monster.computer.contracts import MODIFIERS, Bounds, validate_action


def single_click_requested(instruction):
    """Conservative input budget for explicit English one-click requests.

    This limits input, not target grounding. Recognize terminal "click ... once"
    requests only; compound instructions after "once" use the ordinary workflow.
    """
    return bool(re.search(
        r"\bclick\b[^.!?;\n]{0,120}\bonce\s*[.!]?\s*$",
        instruction, flags=re.IGNORECASE,
    ))


def window_close_requested(instruction):
    """Narrow explicit English intent, never inferred from model output."""
    return bool(re.fullmatch(
        r"\s*(?:please\s+)?close\s+(?:the\s+)?"
        r"(?:(?:current|active|foreground|upfront|frontmost)\s+){1,2}"
        r"(?:chrome(?:\s+browser)?(?:\s+window)?|browser(?:\s+window)?|"
        r"(?:(?:file|windows)\s+)?explorer(?:\s+window)?|window)\s*[.!]?\s*",
        instruction, flags=re.IGNORECASE,
    ))


def validate_proposal_intent(decision, instruction):
    """Narrow OS-launcher consistency check, not general semantic grounding.

    Implicit Start navigation has a layout-independent keyboard representation.
    Reject a contradictory proposal rather than rewriting reviewed input. Explicit
    pointer tasks keep their original contract and still need visual grounding.
    """
    if decision is None or decision.kind != "action":
        return
    action = decision.action or {}
    if action.get("op") == "press" and action.get("key") in MODIFIERS - {"WIN"}:
        raise ValueError("A modifier alone is not a shortcut. Propose one complete chord with all keys.")
    validate_shortcut_description(action, decision.summary)
    validate_quoted_typing(action, decision.summary)
    expected = decision.expected_result or ""
    validate_shortcut_description(action, expected)
    navigation = action in (
        {'op': 'press', 'key': 'HOME'}, {'op': 'press', 'key': 'END'},
        {'op': 'chord', 'keys': ['CTRL', 'HOME']},
        {'op': 'chord', 'keys': ['CTRL', 'END']},
    )
    if navigation and re.search(
        r'\b(?:creat\w*|open\w*)\b[^.!?\n]{0,60}\b(?:new|blank|empty)\s+(?:document|file|tab)\b'
        r'|\bclear\w*\s+(?:the\s+)?(?:current\s+)?(?:content|document|text)\b',
        decision.summary + '\n' + expected, re.IGNORECASE,
    ):
        raise ValueError('Home/End only move the caret; they do not create or clear a document. '
                         'For a new Notepad document propose the complete CTRL+N chord or a visible menu action.')
    if terminal_window_requested(instruction) and (
        re.search(r"\bnew\b[^.!?\n]{0,60}\btab\b", expected, re.IGNORECASE)
        or action == {"op": "chord", "keys": ["CTRL", "SHIFT", "T"]}
    ):
        raise ValueError(
            "A new tab is not the requested new terminal window. When Windows Terminal is "
            "visibly the intended app, propose the complete CTRL+SHIFT+N chord for a new window, "
            "or locate its New Window menu. Do not run shell commands."
        )
    summary = decision.summary
    opens_start = bool(re.search(
        r"\bstart\s+menu\s+(?:(?:will|should|must)\s+)?(?:now\s+)?(?:opens?|appears?)\b"
        r"|\b(?:open|show|display)\s+(?:the\s+)?(?:windows\s+)?start\s+menu\b",
        expected, re.IGNORECASE,
    ))
    claims_launcher = bool(re.search(
        r"\b(?:press|use|using)\s+(?:the\s+)?(?:windows(?:\s+logo)?|win)\s+key\b"
        r"|\bshortcut\s+(?:the\s+)?(?:windows|win)\s+key\b"
        r"|\b(?:click|open|press)\s+(?:on\s+)?(?:the\s+)?"
        r"(?:windows\s+start\s+(?:button|menu)|start\s+menu)\b",
        summary, re.IGNORECASE,
    ))
    if not (opens_start or claims_launcher):
        return
    explicit_pointer = bool(re.search(
        r"\b(?:click|tap)\s+(?:once\s+)?(?:on\s+)?(?:the\s+)?"
        r"(?:windows\s+)?start(?:\s+(?:button|icon|menu))?\b",
        instruction, re.IGNORECASE,
    ))
    if explicit_pointer and action.get("op") in {"click", "move"}:
        return
    if action != {"op": "press", "key": "WIN"}:
        raise ValueError(
            "Opening Windows Start for navigation requires press WIN (the Windows logo key), "
            "not a letter, chord or guessed taskbar click. Propose the correct atomic key "
            "action, or ask for help. No input was authorized or silently substituted."
        )


def approval_reason(decision, instruction=""):
    """User-authorized automatic mode still escalates sensitive/uncertain effects.

    Model classification is fallible. Deterministic escalation and blocked-action
    validation remain in effect; this is not a security boundary for arbitrary apps.
    """
    action = decision.action or {}
    if action == {"op": "chord", "keys": ["ALT", "F4"]}:
        return "Closing a window can discard unsaved work. Review the intended target."
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
    if action.get("op") == "drag":
        return (None if drawing_requested(instruction) and decision.risk == "routine"
                and action.get("button", "left") == "left"
                else "Dragging requires an explicit drawing task and a verified canvas, or review.")
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
        if action.get("key") in {"ENTER", "SPACE", "WIN"} | ROUTINE_CHARACTER_KEYS and decision.risk == "routine":
            return None
        return "This key's effect needs review."
    if (action == {"op": "chord", "keys": NEW_TERMINAL_WINDOW}
            and terminal_window_requested(instruction) and decision.risk == "routine"):
        return None
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


def needs_human_approval(decision, instruction=""):
    return approval_reason(decision, instruction) is not None


def prepare_action(action, observation, *, allow_window_close=False, allow_terminal_window=False):
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
    if action.get("op") == "press" and action.get("key") in MODIFIERS - {"WIN"}:
        raise ValueError("A modifier alone is not a shortcut; provide a complete chord.")
    if action.get("op") == "press" and action.get("key") == "DELETE":
        raise ValueError("Delete is outside the supervised demo.")
    if action.get("op") == "chord":
        keys = action.get("keys", [])
        if not ((allow_window_close and keys == ["ALT", "F4"])
                or (allow_terminal_window and keys == NEW_TERMINAL_WINDOW)) and keys not in (
            ["CTRL", "A"],
            ["CTRL", "C"],
            ["CTRL", "Z"],
            ["CTRL", "N"],
            ["CTRL", "HOME"],
            ["CTRL", "END"],
            ["SHIFT", "LEFT"],
            ["SHIFT", "RIGHT"],
            ["SHIFT", "ENTER"],
        ):
            raise ValueError("Shortcut is outside the supervised demo allowlist.")
    for xkey, ykey in (("x", "y"), ("end_x", "end_y")):
        if xkey in action:
            action[xkey] += observation.bounds.x
            action[ykey] += observation.bounds.y
    return validate_action(action, list(observation.displays))


def scene_matches(before, after, action, *, diagnostics=None):
    def finish(matched, reason, **details):
        if diagnostics is not None:
            diagnostics.update(matched=matched, reason=reason, **details)
        return matched

    if before.bounds != after.bounds:
        return finish(False, "capture_bounds_changed")
    if before.displays != after.displays:
        return finish(False, "display_geometry_changed")
    if not before.foreground or before.foreground != after.foreground:
        return finish(False, "foreground_changed_or_missing")
    old, new = QImage.fromData(before.png), QImage.fromData(after.png)
    if old.isNull() or new.isNull() or old.size() != new.size():
        return finish(False, "invalid_image_or_dimensions_changed")
    x, y = action.get("x"), action.get("y")
    if x is not None:
        region = QRect(x - 20, y - 20, 41, 41).intersected(old.rect())
        if old.copy(region) != new.copy(region):
            return finish(False, "target_patch_changed", patch=[region.x(), region.y(), region.width(), region.height()])
    if action.get("op") == "drag":
        region = QRect(min(action["x"], action["end_x"]) - 20,
                       min(action["y"], action["end_y"]) - 20,
                       abs(action["end_x"] - action["x"]) + 41,
                       abs(action["end_y"] - action["y"]) + 41).intersected(old.rect())
        if old.copy(region) != new.copy(region):
            return finish(False, "drag_path_changed")
    old = old.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio).convertToFormat(
        QImage.Format.Format_RGBA8888
    )
    new = new.scaled(old.size()).convertToFormat(QImage.Format.Format_RGBA8888)
    a, b = bytes(old.constBits()), bytes(new.constBits())
    changed = 0
    pixels = old.width() * old.height()
    threshold = max(1, pixels * 0.002)
    for index in range(pixels):
        offset = index * 4
        if max(abs(a[offset + c] - b[offset + c]) for c in range(3)) > 20:
            changed += 1
            if changed > threshold:
                return finish(False, "whole_scene_changed", changed_pixels=changed,
                              sampled_pixels=pixels, allowed_changed_pixels=threshold,
                              count_is_lower_bound=True)
    return finish(True, "scene_matches", changed_pixels=changed, sampled_pixels=pixels,
                  allowed_changed_pixels=threshold, count_is_lower_bound=False)
