"""Small strict wire protocol: anonymous pipe messages contain JSON, never code."""

import math
from dataclasses import dataclass

BUTTONS = {"left", "right", "middle"}
KEYS = {
    **{chr(c): c for c in range(65, 91)},
    **{str(i): 48 + i for i in range(10)},
    "ENTER": 0x0D,
    "TAB": 0x09,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "CTRL": 0xA2,
    "ALT": 0xA4,
    "SHIFT": 0xA0,
    "WIN": 0x5B,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
}
MODIFIERS = {"CTRL", "ALT", "SHIFT", "WIN"}


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x, y):
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer in [{low}, {high}].")
    return value


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Expected finite number in [{low}, {high}].")
    return value


def validate_action(value: dict, displays: list[Bounds]) -> dict:
    if type(value) is not dict:
        raise ValueError("Action must be an object.")
    schemas = {
        "move": ({"x", "y"}, set()),
        "click": ({"x", "y"}, {"button", "count"}),
        "scroll": ({"x", "y", "delta"}, {"horizontal"}),
        "drag": ({"x", "y", "end_x", "end_y"}, {"duration", "button"}),
        "down": ({"x", "y"}, {"button"}),
        "up": (set(), {"button"}),
        "text": ({"text"}, set()),
        "press": ({"key"}, set()),
        "chord": ({"keys"}, set()),
        "key_down": ({"key"}, set()),
        "key_up": ({"key"}, set()),
    }
    op = value.get("op")
    if type(op) is not str or op not in schemas:
        raise ValueError("Unknown operation; only Mouse and Keyboard operations are accepted.")
    required, optional = schemas[op]
    fields = set(value) - {"op"}
    if not required <= fields or fields - required - optional:
        raise ValueError("Missing or unknown action fields.")
    for xkey, ykey in (("x", "y"), ("end_x", "end_y")):
        if xkey in value:
            x = integer(value[xkey], -100000, 100000)
            y = integer(value[ykey], -100000, 100000)
            if not any(b.contains(x, y) for b in displays):
                raise ValueError("Point is outside actual displays (including monitor gaps).")
    if "button" in value and value["button"] not in BUTTONS:
        raise ValueError("Unsupported mouse button.")
    if "count" in value:
        integer(value["count"], 1, 2)
    if "delta" in value:
        integer(value["delta"], -1200, 1200)
    if "horizontal" in value and type(value["horizontal"]) is not bool:
        raise ValueError("horizontal must be Boolean.")
    if "duration" in value:
        number(value["duration"], 0.05, 2.0)
    if "text" in value:
        text = value["text"]
        if type(text) is not str or not 1 <= len(text) <= 2000:
            raise ValueError("Text must contain 1–2000 characters.")
        if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
            raise ValueError(
                "Use key operations for control characters; lone surrogates are invalid."
            )
    if "key" in value:
        if type(value["key"]) is not str or value["key"] not in KEYS:
            raise ValueError("Unsupported named key.")
        if op in ("key_down", "key_up") and value["key"] not in MODIFIERS:
            raise ValueError("Only modifiers may be held explicitly.")
    if "keys" in value:
        keys = value["keys"]
        if (
            type(keys) is not list
            or not 2 <= len(keys) <= 4
            or any(type(k) is not str or k not in KEYS for k in keys)
            or len(set(keys)) != len(keys)
            or any(k not in MODIFIERS for k in keys[:-1])
            or keys[-1] in MODIFIERS
        ):
            raise ValueError("Chord needs unique modifiers followed by one named key.")
    return dict(value)


def absolute_point(x: int, y: int, bounds: Bounds) -> tuple[int, int]:
    if not bounds.contains(x, y):
        raise ValueError("Point outside virtual desktop.")
    return (
        round((x - bounds.x) * 65535 / max(1, bounds.width - 1)),
        round((y - bounds.y) * 65535 / max(1, bounds.height - 1)),
    )
