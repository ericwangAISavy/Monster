"""Strict next-action schema. A decision is data; this module has no executor dependency."""

from copy import deepcopy

from monster.agent.keyboard import NEW_TERMINAL_WINDOW, terminal_window_requested
from monster.computer.contracts import KEYS, MODIFIERS, Bounds, validate_action
from monster.providers.base import Decision, ProviderError

# Flatten provider schema variants into exactly the same three categories as local tools.
ACTION_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"op": {"const": op}, **properties},
            "required": ["op", *properties],
            "additionalProperties": False,
        }
        for op, properties in [
            ("move", {"x_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                      "y_1000": {"type": "integer", "minimum": 0, "maximum": 1000}}),
            (
                "click",
                {
                    "x_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "y_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "button": {"enum": ["left", "right", "middle"]},
                    "count": {"type": "integer", "minimum": 1, "maximum": 2},
                },
            ),
            (
                "scroll",
                {
                    "x_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "y_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "delta": {"type": "integer", "minimum": -1200, "maximum": 1200},
                    "horizontal": {"type": "boolean"},
                },
            ),
            ("text", {"text": {"type": "string", "minLength": 1, "maxLength": 2000}}),
            ("press", {"key": {"enum": [key for key in KEYS if key != "DELETE" and key not in MODIFIERS - {"WIN"}]}}),
            (
                "chord",
                {
                    "keys": {
                        "enum": [
                            ["CTRL", "A"],
                            ["CTRL", "C"],
                            ["CTRL", "Z"],
                            ["CTRL", "HOME"],
                            ["CTRL", "END"],
                            ["SHIFT", "LEFT"],
                            ["SHIFT", "RIGHT"],
                            ["SHIFT", "ENTER"],
                            ["ALT", "F4"],
                        ]
                    }
                },
            ),
            ("screenshot", {}),
        ]
    ]
}
DECISION_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"const": "action"},
                "summary": {"type": "string", "minLength": 1, "maxLength": 300},
                "action": ACTION_SCHEMA,
                "risk": {"enum": ["routine", "sensitive", "uncertain"]},
                "expected_result": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["kind", "summary", "action", "risk", "expected_result"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"enum": ["complete", "clarify", "unable"]},
                "summary": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["kind", "summary"],
            "additionalProperties": False,
        },
    ]
}


def decision_schema_for_draft():
    """Constrain generation as well as enforcing draft policy at dispatch."""
    schema = deepcopy(DECISION_SCHEMA)
    variants = schema["oneOf"][0]["properties"]["action"]["oneOf"]
    variants[:] = [v for v in variants if v["properties"]["op"]["const"] != "press"]
    for variant in variants:
        op = variant["properties"]["op"]["const"]
        if op == "text":
            variant["properties"]["text"]["description"] = (
                "The entire requested draft. Typing this whole Unicode string is ONE action."
            )
        elif op == "chord":
            variant["properties"]["keys"]["enum"] = [["SHIFT", "ENTER"]]
    return schema


def decision_schema_for_navigation(instruction):
    schema = deepcopy(DECISION_SCHEMA)
    if terminal_window_requested(instruction):
        for variant in schema["oneOf"][0]["properties"]["action"]["oneOf"]:
            if variant["properties"]["op"]["const"] == "chord":
                variant["properties"]["keys"]["enum"].append(list(NEW_TERMINAL_WINDOW))
    return schema


def parse_decision(value, width: int, height: int, *, coordinate_space="image_pixels") -> Decision:
    if coordinate_space not in {"image_pixels", "normalized_1000"}:
        raise ProviderError("Model action failed validation: unknown coordinate space.")
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ProviderError("Model action failed validation: invalid image dimensions.")
    coordinate_source = None
    if (
        type(value) is not dict
        or type(value.get("kind")) is not str
        or value["kind"] not in {"action", "complete", "clarify", "unable"}
    ):
        raise ProviderError("Model returned an invalid decision.")
    summary = value.get("summary")
    if type(summary) is not str or not 1 <= len(summary.strip()) <= 300:
        raise ProviderError("Model returned an invalid summary.")
    required = {"kind", "summary", "action"} if value["kind"] == "action" else {"kind", "summary"}
    allowed = required | {"risk", "expected_result"} if value["kind"] == "action" else required
    if not required <= set(value) or set(value) - allowed:
        raise ProviderError("Model decision contains missing or unknown fields.")
    risk = value.get("risk")
    if "risk" in value and (
        type(risk) is not str or risk not in {"routine", "sensitive", "uncertain"}
    ):
        raise ProviderError("Model returned an invalid action risk classification.")
    expected = value.get("expected_result")
    if "expected_result" in value and (
        type(expected) is not str or not 1 <= len(expected.strip()) <= 300
    ):
        raise ProviderError("Model returned an invalid expected result.")
    action = value.get("action")
    if action is not None:
        if type(action) is not dict:
            raise ProviderError("Model returned an invalid action.")
        if action != {"op": "screenshot"}:
            if type(action.get("op")) is not str or action["op"] not in {
                "move",
                "click",
                "scroll",
                "text",
                "press",
                "chord",
            }:
                raise ProviderError("Unsupported model action.")
            if coordinate_space == "normalized_1000" and action.get("op") in {"move", "click", "scroll"}:
                if any(key in action for key in ("x", "y", "end_x", "end_y")) or any(
                    type(action.get(key)) is not int or not 0 <= action[key] <= 1000
                    for key in ("x_1000", "y_1000")
                ):
                    raise ProviderError(
                        "Model action failed validation: use integer x_1000/y_1000 in [0,1000], not pixel coordinates."
                    )
                coordinate_source = {
                    "space": "normalized_1000", "model_action": deepcopy(action),
                    "image_size": {"width": width, "height": height},
                    "mapping": "round(value * (image_size - 1) / 1000)",
                }
                action = dict(action)
                action["x"] = round(action.pop("x_1000") * (width - 1) / 1000)
                action["y"] = round(action.pop("y_1000") * (height - 1) / 1000)
            try:
                validate_action(action, [Bounds(0, 0, width, height)])
            except (ValueError, TypeError):
                raise ProviderError(
                    "Model action failed validation; nothing was executed."
                ) from None
    elif value["kind"] == "action":
        raise ProviderError("Action decision requires an action.")
    return Decision(value["kind"], summary.strip(), action, risk, expected, coordinate_source)
