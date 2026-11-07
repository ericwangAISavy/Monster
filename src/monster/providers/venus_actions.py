"""Bounded UI-Venus native-format parsing and inert, frame-bound preview data.

No model output is evaluated or executed. This module has no input-worker port.
"""

from __future__ import annotations

import ast
import hashlib
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from monster.computer.contracts import KEYS, Bounds, validate_action
from monster.providers.base import ProviderError

UPSTREAM_REVISION = "6d5f8baf3d28868b3e32fefa9da501e99964a8c5"
UPSTREAM_SOURCE_SHA256 = "844cb2efb504c7febca83cede6dd28a55c46c5701cd32c627d01e300df6bf812"
NORM = 999
MAX_PNG_BYTES = 32 * 1024 * 1024
SCHEMAS = {
    **{name: (set(), {"box"}) for name in (
        "Click", "DoubleClick", "TripleClick", "RightClick", "MiddleClick", "MouseDown", "MouseUp")},
    "Hover": ({"box"}, set()), "Drag": ({"end"}, {"start"}),
    "Swipe": ({"amount", "axis"}, set()), "Type": (set(), {"content"}),
    "Hotkey": ({"keys"}, {"repeat"}), "KeyDown": ({"keys"}, set()),
    "KeyUp": ({"keys"}, set()), "Wait": (set(), set()),
    "CallUser": (set(), {"content"}), "Finished": (set(), {"content"}),
}
COORDINATES = frozenset({"box", "start", "end"})


class UIVenusActionError(ProviderError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"UI-Venus preview rejected ({code}).")


def _require(condition, code):
    if not condition:
        raise UIVenusActionError(code)


def _number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _point(value):
    return (type(value) is tuple and len(value) == 2
            and all(_number(v) and 0 <= v <= NORM for v in value))


def normalized_point(value, width, height):
    _require(all(type(n) is int and 0 < n <= 100000 for n in (width, height))
             and width * height <= 40_000_000, "image_dimensions")
    _require(_point(value), "coordinate_range")
    # Match pinned upstream int(value * dimension / 999), then clamp endpoint.
    return (min(width - 1, int(value[0] * width / NORM)),
            min(height - 1, int(value[1] * height / NORM)))


def _geometry(bounds):
    return (isinstance(bounds, Bounds)
            and all(type(v) is int for v in (bounds.x, bounds.y, bounds.width, bounds.height))
            and bounds.width > 0 and bounds.height > 0
            and -100000 <= bounds.x <= bounds.x + bounds.width - 1 <= 100000
            and -100000 <= bounds.y <= bounds.y + bounds.height - 1 <= 100000)


def validate_png(png, width, height):
    _require(type(png) is bytes and 33 <= len(png) <= MAX_PNG_BYTES, "image_bytes")
    _require(png[:8] == b"\x89PNG\r\n\x1a\n" and png[8:16] == b"\x00\x00\x00\rIHDR", "png_header")
    _require(struct.unpack(">II", png[16:24]) == (width, height), "image_dimensions")
    _require(0 < width * height <= 40_000_000, "image_dimensions")
    from PySide6.QtGui import QImage
    decoded = QImage.fromData(png, "PNG")
    _require(not decoded.isNull() and (decoded.width(), decoded.height()) == (width, height), "png_decode")


@dataclass(frozen=True)
class FrameBinding:
    observation_id: str
    image_sha256: str
    bounds: Bounds
    displays: tuple[Bounds, ...]
    captured_at: float
    foreground_guard: int = field(default=0, repr=False)

    def __post_init__(self):
        _require(type(self.observation_id) is str and 1 <= len(self.observation_id) <= 128, "frame_identity")
        _require(type(self.image_sha256) is str and bool(re.fullmatch("[a-f0-9]{64}", self.image_sha256)), "image_hash")
        _require(_geometry(self.bounds) and self.bounds.width * self.bounds.height <= 40_000_000, "geometry")
        _require(type(self.displays) is tuple and 1 <= len(self.displays) <= 64
                 and all(_geometry(b) for b in self.displays), "geometry")
        _require(_number(self.captured_at) and self.captured_at >= 0, "frame_timestamp")
        _require(type(self.foreground_guard) is int and 0 <= self.foreground_guard < 2**64, "foreground_guard")

    @classmethod
    def from_observation(cls, observation):
        binding = cls(observation.id, observation.sha256, observation.bounds,
                      observation.displays, observation.captured_at, observation.foreground)
        validate_png(observation.png, binding.bounds.width, binding.bounds.height)
        _require(hashlib.sha256(observation.png).hexdigest() == binding.image_sha256, "image_hash")
        return binding


@dataclass(frozen=True)
class NativeAction:
    name: str
    arguments: tuple[tuple[str, object], ...]

    def canonical_text(self):
        parts = []
        for key, value in self.arguments:
            # Native Hotkey expects a list; stored values remain immutable tuples.
            parts.append(f"{key}={list(value)!r}" if key == "keys" else f"{key}={value!r}")
        return f"{self.name}({', '.join(parts)})"


def _literal(node, depth=0):
    _require(depth <= 8, "literal_depth")
    if isinstance(node, ast.Constant) and type(node.value) in (str, int, float, bool, type(None)):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_literal(item, depth + 1) for item in node.elts)
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float)):
        return node.operand.value if isinstance(node.op, ast.UAdd) else -node.operand.value
    raise UIVenusActionError("nonliteral_argument")


def parse_action_response(response):
    _require(type(response) is str and 1 <= len(response) <= 65536, "response_size")
    text = response.strip()
    if text.casefold().startswith("<think>"):
        end = text.casefold().find("</think>")
        _require(end >= 0, "incomplete_thought_block")
        text = text[end + len("</think>"):].strip()
    if text.casefold().startswith("<action>"):
        end = text.casefold().rfind("</action>")
        _require(end >= len("<action>") and not text[end + len("</action>"):].strip(), "action_block")
        text = text[len("<action>"):end].strip()
    _require(1 <= len(text) <= 8192, "action_size")
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, TypeError, RecursionError):
        raise UIVenusActionError("action_syntax") from None
    _require(sum(1 for _ in ast.walk(tree)) <= 512, "action_complexity")
    call = tree.body
    _require(isinstance(call, ast.Call) and isinstance(call.func, ast.Name), "direct_call_required")
    _require(not call.args, "keyword_arguments_required")
    if call.func.id == "Sequence":
        raise UIVenusActionError("open_loop_sequence_unsupported")
    _require(call.func.id in SCHEMAS, "unknown_action")
    values = {}
    for keyword in call.keywords:
        _require(keyword.arg is not None and keyword.arg not in values, "duplicate_or_expanded_argument")
        if keyword.arg in COORDINATES:
            _require(isinstance(keyword.value, ast.Tuple), "coordinate_tuple_required")
        if keyword.arg == "keys":
            _require(isinstance(keyword.value, ast.List), "key_list_required")
        values[keyword.arg] = _literal(keyword.value)
    required, optional = SCHEMAS[call.func.id]
    _require(required <= set(values) <= required | optional, "action_fields")
    for key, value in values.items():
        if key in COORDINATES:
            _require(_point(value), "coordinate_range")
        elif key == "keys":
            _require(type(value) is tuple and 1 <= len(value) <= 128 and all(
                type(v) is str and 1 <= len(v.strip()) <= 64 for v in value), "keys")
        elif key == "repeat":
            _require(type(value) is int and 1 <= value <= 128, "repeat")
        elif key == "amount":
            _require(type(value) is int and -4096 <= value <= 4096, "scroll_amount")
        elif key == "axis":
            _require(type(value) is str and value in {"vertical", "horizontal"}, "scroll_axis")
        elif key == "content":
            _require(type(value) is str and len(value) <= 2000
                     and not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "content")
    return NativeAction(call.func.id, tuple(values.items()))


def _key(value):
    aliases = {"control": "CTRL", "escape": "ESC", "return": "ENTER", "super": "WIN",
               "meta": "WIN", "page_up": "PAGEUP", "page_down": "PAGEDOWN"}
    key = aliases.get(value.strip().casefold(), value.strip().upper())
    _require(key in KEYS, "unsupported_key")
    return key


def _redact(action, include_content):
    if action is None:
        return None
    result = dict(action)
    if "keys" in result:
        result["keys"] = list(result["keys"])
    if "text" in result and not include_content:
        result["text_characters"] = len(result.pop("text"))
    return result


@dataclass(frozen=True)
class PreviewDecision:
    kind: str
    task_id: str
    generation: int
    frame: FrameBinding
    image_action: Mapping | None = field(repr=False)
    desktop_action: Mapping | None = field(repr=False)
    source: Mapping
    summary: str = field(default="", repr=False)
    schema_version: int = field(default=1, init=False)
    verification: str = field(default="unverified", init=False)
    execution_allowed: bool = field(default=False, init=False)

    def to_dict(self, *, include_content=False):
        result = {
            "schema_version": self.schema_version, "kind": self.kind, "task_id": self.task_id,
            "generation": self.generation, "observation_id": self.frame.observation_id,
            "image_sha256": self.frame.image_sha256,
            "image_size_px": [self.frame.bounds.width, self.frame.bounds.height],
            "desktop_origin_px": [self.frame.bounds.x, self.frame.bounds.y],
            "image_action": _redact(self.image_action, include_content),
            "desktop_action": _redact(self.desktop_action, include_content),
            "source": dict(self.source), "verification": "unverified", "execution_allowed": False,
        }
        if include_content:
            result["summary"] = self.summary
        return result


def normalize_preview(call, frame, task_id, generation):
    _require(type(task_id) is str and 1 <= len(task_id) <= 128, "task_identity")
    _require(type(generation) is int and 0 <= generation < 2**31, "task_generation")
    _require(isinstance(frame, FrameBinding), "frame_binding")
    name, args = call.name, dict(call.arguments)
    points = {key: normalized_point(value, frame.bounds.width, frame.bounds.height)
              for key, value in args.items() if key in COORDINATES}
    source = MappingProxyType({
        "provider": "ui_venus_computer", "native_action": name,
        "upstream_revision": UPSTREAM_REVISION, "coordinate_space": "ui_venus_normalized_999",
        "normalized_points": tuple((key, value) for key, value in args.items() if key in COORDINATES),
        "mapping": "min(size - 1, int(value * size / 999)) + desktop_origin",
    })
    kind, action = "action", None
    if name in {"Finished", "CallUser", "Wait"}:
        kind = {"Finished": "completion_claim", "CallUser": "needs_user", "Wait": "observe"}[name]
    elif name in {"KeyDown", "KeyUp", "MouseDown", "MouseUp", "TripleClick", "Swipe"}:
        raise UIVenusActionError("native_semantics_unsupported")
    elif name in {"Click", "DoubleClick", "RightClick", "MiddleClick", "Hover"}:
        _require("box" in points, "cursor_anchor_required")
        x, y = points["box"]
        action = {"op": "move" if name == "Hover" else "click", "x": x, "y": y}
        if name != "Hover":
            action.update(button={"RightClick": "right", "MiddleClick": "middle"}.get(name, "left"),
                          count=2 if name == "DoubleClick" else 1)
    elif name == "Drag":
        _require("start" in points, "cursor_anchor_required")
        action = {"op": "drag", "x": points["start"][0], "y": points["start"][1],
                  "end_x": points["end"][0], "end_y": points["end"][1], "duration": .5, "button": "left"}
    elif name == "Type":
        action = {"op": "text", "text": args.get("content", "")}
    elif name == "Hotkey":
        _require(args.get("repeat", 1) == 1, "repeated_hotkey_unsupported")
        keys = tuple(_key(key) for key in args["keys"])
        action = ({"op": "press", "key": keys[0]} if len(keys) == 1
                  else {"op": "chord", "keys": list(keys)})
    else:
        raise UIVenusActionError("native_semantics_unsupported")
    desktop = None
    if action is not None:
        try:
            validate_action(action, [Bounds(0, 0, frame.bounds.width, frame.bounds.height)])
            desktop = dict(action)
            for xkey, ykey in (("x", "y"), ("end_x", "end_y")):
                if xkey in desktop:
                    desktop[xkey] += frame.bounds.x
                    desktop[ykey] += frame.bounds.y
            validate_action(desktop, list(frame.displays))
        except (ValueError, TypeError):
            raise UIVenusActionError("unsupported_action_payload_or_display_gap") from None
        if "keys" in action:
            action["keys"] = tuple(action["keys"])
            desktop["keys"] = tuple(desktop["keys"])
        action, desktop = MappingProxyType(action), MappingProxyType(desktop)
    return PreviewDecision(kind, task_id, generation, frame, action, desktop, source, args.get("content", ""))


def parse_preview(response, frame, task_id, generation):
    return normalize_preview(parse_action_response(response), frame, task_id, generation)
