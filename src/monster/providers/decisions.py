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
            "required": ["op", *(name for name in properties if name not in optional)],
            "additionalProperties": False,
        }
        for op, properties, optional in [
            (op, properties, {"path_1000"} if op == "drag" else set())
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
                    "delta": {"type": "integer", "minimum": -1200, "maximum": 1200,
                              "description": "Wheel amount: NEGATIVE scrolls down, positive scrolls up; 120 = one notch."},
                    "horizontal": {"type": "boolean"},
                },
            ),
            ("drag", {
                "x_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                "y_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                "end_x_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                "end_y_1000": {"type": "integer", "minimum": 0, "maximum": 1000},
                "button": {"const": "left"},
                "duration": {"type": "number", "minimum": 0.05, "maximum": 2.0},
                "path_1000": {
                    "type": "array", "minItems": 1, "maxItems": 24,
                    "description": "Optional points between start and end, for curves and circles.",
                    "items": {"type": "array", "minItems": 2, "maxItems": 2,
                              "items": {"type": "integer", "minimum": 0, "maximum": 1000}},
                },
            }),
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
                            ["CTRL", "N"],
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
        ]]
    ]
}
PREVIOUS_STEP = {
    "type": "object",
    "description": "Only when asked: did the previous action visibly achieve its expected result?",
    "properties": {
        "status": {"enum": ["succeeded", "failed", "uncertain"]},
        "evidence": {"type": "string", "minLength": 1, "maxLength": 300},
    },
    "required": ["status", "evidence"],
    "additionalProperties": False,
}
FOLLOW_UPS = {
    "type": "array", "maxItems": 3,
    "description": "Optional keyboard steps (text, press, chord) to run right after the action, "
                   "for a sure sequence such as WIN, then type an app name, then ENTER.",
    "items": {"oneOf": [v for v in ACTION_SCHEMA["oneOf"]
                        if v["properties"]["op"]["const"] in {"text", "press", "chord"}]},
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
                "previous_step": PREVIOUS_STEP,
                "then": FOLLOW_UPS,
            },
            "required": ["kind", "summary", "action", "risk", "expected_result"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"enum": ["complete", "clarify", "unable"]},
                "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
                "previous_step": PREVIOUS_STEP,
            },
            "required": ["kind", "summary"],
            "additionalProperties": False,
        },
    ]
}


def decision_schema_for_draft():
    """Constrain generation as well as enforcing draft policy at dispatch."""
    schema = deepcopy(DECISION_SCHEMA)
    schema["oneOf"][0]["properties"].pop("then", None)  # Drafts never batch steps.
    variants = schema["oneOf"][0]["properties"]["action"]["oneOf"]
    variants[:] = [v for v in variants if v["properties"]["op"]["const"] not in {"press", "drag"}]
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
    if type(summary) is not str or not summary.strip():
        raise ProviderError("Model returned an invalid summary.")
    # A final report can carry the information the task asked for; a step summary is only
    # a label, so an over-long one is shortened rather than costing a whole planning call.
    limit = 300 if value["kind"] == "action" else 1200
    if len(summary.strip()) > limit:
        if value["kind"] != "action":
            raise ProviderError("Model returned an invalid summary.")
        summary = summary.strip()[:limit - 1].rstrip() + "…"
    required = {"kind", "summary", "action"} if value["kind"] == "action" else {"kind", "summary"}
    allowed = ((required | {"risk", "expected_result", "then"}) if value["kind"] == "action"
               else required) | {"previous_step"}
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
                "drag",
                "text",
                "press",
                "chord",
            }:
                raise ProviderError("Unsupported model action.")
            if coordinate_space == "normalized_1000" and action.get("op") in {"move", "click", "scroll", "drag"}:
                coordinates = {"x_1000": ("x", width), "y_1000": ("y", height)}
                if action["op"] == "drag":
                    coordinates.update(end_x_1000=("end_x", width), end_y_1000=("end_y", height))
                    path = action.get("path_1000")
                    if path is not None and (
                        type(path) is not list or not 1 <= len(path) <= 24
                        or any(type(p) is not list or len(p) != 2
                               or any(type(v) is not int or not 0 <= v <= 1000 for v in p)
                               for p in path)):
                        raise ProviderError("Model action failed validation: path_1000 needs "
                                            "1-24 [x, y] integer pairs in [0,1000].")
                if any(key in action for key in ("x", "y", "end_x", "end_y")) or any(
                    type(action.get(key)) is not int or not 0 <= action[key] <= 1000
                    for key in coordinates
                ):
                    raise ProviderError(
                        "Model action failed validation: use integer x_1000/y_1000 (and drag end_x_1000/end_y_1000) "
                        "in [0,1000], not pixel coordinates."
                    )
                coordinate_source = {
                    "space": "normalized_1000", "model_action": deepcopy(action),
                    "image_size": {"width": width, "height": height},
                    "mapping": "round(value * (image_size - 1) / 1000)",
                }
                action = dict(action)
                for source, (target, extent) in coordinates.items():
                    action[target] = round(action.pop(source) * (extent - 1) / 1000)
                if "path_1000" in action:
                    action["path"] = [[round(x * (width - 1) / 1000), round(y * (height - 1) / 1000)]
                                      for x, y in action.pop("path_1000")]
            try:
                validate_action(action, [Bounds(0, 0, width, height)])
            except (ValueError, TypeError):
                raise ProviderError(
                    "Model action failed validation; nothing was executed."
                ) from None
    elif value["kind"] == "action":
        raise ProviderError("Action decision requires an action.")
    previous = value.get("previous_step")
    if previous is not None and (
        type(previous) is not dict or set(previous) != {"status", "evidence"}
        or previous.get("status") not in {"succeeded", "failed", "uncertain"}
        or type(previous.get("evidence")) is not str
        or not 1 <= len(previous["evidence"].strip()) <= 300
    ):
        previous = None  # A malformed self-check falls back to the independent check.
    then = ()
    if value["kind"] == "action" and value.get("then"):
        steps = value["then"]
        try:
            if type(steps) is not list or len(steps) > 3:
                raise ValueError
            for step in steps:
                if type(step) is not dict or step.get("op") not in {"text", "press", "chord"}:
                    raise ValueError
                validate_action(step, [Bounds(0, 0, width, height)])
            then = tuple(dict(step) for step in steps)
        except (ValueError, TypeError):
            then = ()  # A bad follow-up list is dropped; the first action still stands.
    return Decision(value["kind"], summary.strip(), action, risk, expected, coordinate_source,
                    previous, then)
