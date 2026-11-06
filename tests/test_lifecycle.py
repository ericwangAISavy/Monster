from monster.lifecycle import MockTaskController, State


def test_mock_cancel_prevents_late_completion(qtbot):
    controller = MockTaskController(interval_ms=10)
    completions, states = [], []
    controller.finished.connect(completions.append)
    controller.state_changed.connect(states.append)
    assert controller.submit("Write a note")
    assert not controller.submit("Concurrent task")
    controller.cancel()
    qtbot.wait(50)
    controller._advance()  # Even a stale delivered callback cannot finish a cancelled task.
    assert completions == []
    assert states[-1] == State.IDLE
    assert not controller.active


def test_pause_and_resume(qtbot):
    controller = MockTaskController(interval_ms=20)
    done = []
    controller.finished.connect(done.append)
    controller.submit("Test")
    controller.toggle_pause()
    qtbot.wait(70)
    assert not done and controller.paused
    controller.toggle_pause()
    qtbot.waitUntil(lambda: len(done) == 1)
    assert "Demo" in done[0] and "no computer actions" in done[0]


def test_approval_is_explicit_and_stale_answers_ignored(qtbot):
    controller = MockTaskController(interval_ms=10)
    requests = []
    controller.approval_requested.connect(lambda rid, text: requests.append(rid))
    controller.request_demo("approval")
    qtbot.wait(40)
    assert controller.active and not controller.timer.isActive()
    controller.respond("wrong-id", "approve")
    assert not controller.timer.isActive()
    controller.respond(requests[-1], "reject")
    assert not controller.active
    controller.respond(requests[-1], "approve")
    assert not controller.active
