"""Strict next-action schema. A decision is data; this module has no executor dependency."""

from monster.computer.contracts import Bounds, validate_action
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
            ("press", {"key": {"type": "string"}}),
            (
                "chord",
                {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
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
            },
            "required": ["kind", "summary", "action"],
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
    if set(value) != required:
        raise ProviderError("Model decision contains missing or unknown fields.")
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
    return Decision(value["kind"], summary.strip(), action)
