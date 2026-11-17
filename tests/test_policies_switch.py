"""The safety-policy switch: off by default for saved settings; missions always enforce."""

import json
from dataclasses import replace

import pytest
from test_supervised import controller, observation

from monster.agent.validation import prepare_action
from monster.providers.base import Decision, Verification
from monster.providers.config import load
from monster.providers.decisions import decision_schema_for_navigation


def run(decisions, instruction, *, enforce):
    engine, bridge, model = controller(decisions)
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True,
                            enforce_policies=enforce)

    async def completion(*args):
        return Verification("succeeded", "Done")

    model.verify_completion = completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    return engine, bridge, model


SEND = Decision("action", "Send the message", {"op": "press", "key": "ENTER"}, risk="sensitive",
                expected_result="Message sent")


@pytest.mark.parametrize("enforce", [True, False])
def test_sensitive_step_stops_only_when_policies_apply(qtbot, enforce):
    engine, bridge, _ = run([SEND, Decision("complete", "Sent")],
                            "Send the drafted reply in the open Slack window", enforce=enforce)
    done = []
    engine.finished.connect(done.append)
    engine.failed.connect(done.append)
    try:
        engine.submit("Send the drafted reply in the open Slack window")
        qtbot.waitUntil(lambda: bool(done), timeout=6000)
        if enforce:
            assert not bridge.executed and "requires review" in done[0]
        else:
            assert [a[0] for a in bridge.executed] == [{"op": "press", "key": "ENTER"}]
            assert "Completed" in done[0]
    finally:
        engine.close()


def test_write_and_send_task_may_press_enter_when_policies_are_off(qtbot):
    typing = Decision("action", "Type the message", {"op": "text", "text": "hello team"},
                      risk="routine", expected_result="hello team in the box")
    task = "write the message hello team in the open Slack chat and send it"
    engine, bridge, _ = run([typing, SEND, Decision("complete", "Sent")], task, enforce=False)
    done = []
    engine.finished.connect(done.append)
    engine.failed.connect(done.append)
    try:
        engine.submit(task)
        qtbot.waitUntil(lambda: bool(done), timeout=6000)
        assert [a[0]["op"] for a in bridge.executed] == ["text", "press"]
    finally:
        engine.close()


def test_missions_always_enforce_policies():
    engine, _, _ = controller([])
    try:
        engine.config = replace(engine.config, enforce_policies=False)
        assert not engine.policies_on
        engine.action_gate = True
        assert engine.policies_on
    finally:
        engine.close()


def test_content_and_shortcut_filters_follow_the_switch():
    url = {"op": "text", "text": "https://example.com"}
    with pytest.raises(ValueError):
        prepare_action(url, observation())
    assert prepare_action(url, observation(), enforce=False)["text"] == "https://example.com"
    delete = {"op": "press", "key": "DELETE"}
    with pytest.raises(ValueError):
        prepare_action(delete, observation())
    assert prepare_action(delete, observation(), enforce=False) == delete
    chord = {"op": "chord", "keys": ["CTRL", "L"]}
    with pytest.raises(ValueError):
        prepare_action(chord, observation())
    assert prepare_action(chord, observation(), enforce=False) == chord


def test_unrestricted_schema_offers_delete_and_free_chords():
    variants = decision_schema_for_navigation("open notepad", enforce=False)["oneOf"][0]["properties"]["action"]["oneOf"]
    press = next(v for v in variants if v["properties"]["op"]["const"] == "press")
    chord = next(v for v in variants if v["properties"]["op"]["const"] == "chord")
    assert "DELETE" in press["properties"]["key"]["enum"]
    assert chord["properties"]["keys"]["type"] == "array"


def test_saved_settings_default_to_policies_off(tmp_path):
    assert load(tmp_path / "missing.json").enforce_policies is False
    path = tmp_path / "ollama.json"
    path.write_text(json.dumps({"mode": "demo", "enforce_policies": True}), encoding="utf-8")
    assert load(path).enforce_policies is True
