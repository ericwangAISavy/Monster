"""Strict next-action schema. A decision is data; this module has no executor dependency."""

from monster.computer.contracts import KEYS, Bounds, validate_action
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
            ("move", {"x": {"type": "integer"}, "y": {"type": "integer"}}),
            (
                "click",
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"enum": ["left", "right", "middle"]},
                    "count": {"type": "integer", "minimum": 1, "maximum": 2},
                },
            ),
            (
                "scroll",
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "delta": {"type": "integer", "minimum": -1200, "maximum": 1200},
                    "horizontal": {"type": "boolean"},
                },
            ),
            ("text", {"text": {"type": "string", "minLength": 1, "maxLength": 2000}}),
            ("press", {"key": {"enum": [key for key in KEYS if key != "DELETE"]}}),
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


def parse_decision(value, width: int, height: int) -> Decision:
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
            try:
                validate_action(action, [Bounds(0, 0, width, height)])
            except (ValueError, TypeError):
                raise ProviderError(
                    "Model action failed validation; nothing was executed."
                ) from None
    elif value["kind"] == "action":
        raise ProviderError("Action decision requires an action.")
    return Decision(value["kind"], summary.strip(), action, risk, expected)
