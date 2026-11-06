import threading

import pytest

from monster.computer.contracts import Bounds, absolute_point, validate_action
from monster.computer.executor import InputExecutor
from monster.computer.windows import Input


class FakeWindows:
    def __init__(self):
        self.events = []
        self.monitors = [Bounds(-100, 0, 100, 100), Bounds(0, 0, 200, 100)]
        self.ready = True
        self.held = False
        self.fail_release = False
        self.on_event = lambda: None

    def desktop_ready(self):
        return self.ready

    def physically_held(self):
        return self.held

    def displays(self):
        return list(self.monitors)

    def record(self, *event):
        self.events.append(event)
        self.on_event()

    def move(self, x, y):
        self.record("move", x, y)

    def mouse(self, button, down):
        if not down and self.fail_release:
            raise RuntimeError("Release failed")
        self.record("mouse", button, down)

    def key(self, key, down):
        self.record("key", key, down)

    def unicode(self, unit, down):
        self.record("unicode", unit, down)

    def scroll(self, delta, horizontal):
        self.record("scroll", delta, horizontal)


def armed():
    backend = FakeWindows()
    executor = InputExecutor(backend)
    generation = executor.arm()
    return backend, executor, generation


@pytest.mark.parametrize(
    "action",
    [
        {"op": "exec", "code": "anything"},
        {"op": "move", "x": True, "y": 0},
        {"op": "move", "x": float("nan"), "y": 0},
        {"op": "click", "x": 999, "y": 0},
        {"op": "click", "x": 10, "y": 10, "extra": 1},
        {"op": "text", "text": ""},
        {"op": "text", "text": "x" * 2001},
        {"op": "text", "text": "line\ncommand"},
        {"op": "text", "text": "\ud800"},
        {"op": "press", "key": 13},
        {"op": "key_down", "key": "A"},
        {"op": "chord", "keys": ["CTRL", "CTRL", "A"]},
        {"op": "chord", "keys": ["A", "CTRL"]},
        {"op": "drag", "x": 1, "y": 1, "end_x": 5, "end_y": 5, "duration": 50},
    ],
)
def test_invalid_actions_never_insert(action):
    backend, executor, generation = armed()
    result = executor.execute(action, generation)
    assert result["status"] == "rejected"
    assert not backend.events
    assert not executor.armed


def test_coordinate_endpoints_and_gap_rejection():
    bounds = Bounds(-1920, -200, 3840, 1280)
    assert absolute_point(-1920, -200, bounds) == (0, 0)
    assert absolute_point(1919, 1079, bounds) == (65535, 65535)
    with pytest.raises(ValueError):
        absolute_point(1920, 1080, bounds)
    with pytest.raises(ValueError):
        validate_action(
            {"op": "move", "x": 5, "y": 5}, [Bounds(-20, 0, 20, 20), Bounds(20, 0, 20, 20)]
        )


def test_input_layout_has_expected_windows_size():
    import ctypes

    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_unicode_surrogate_pairs_and_ordered_chord():
    backend, executor, generation = armed()
    assert executor.execute({"op": "text", "text": "A😀文"}, generation)["status"] == "inserted"
    units = [event[1] for event in backend.events if event[2]]
    assert units == [0x41, 0xD83D, 0xDE00, 0x6587]
    assert not executor.owned
    backend.events.clear()
    executor.execute({"op": "chord", "keys": ["CTRL", "SHIFT", "A"]}, generation)
    assert backend.events == [
        ("key", "CTRL", True),
        ("key", "SHIFT", True),
        ("key", "A", True),
        ("key", "A", False),
        ("key", "SHIFT", False),
        ("key", "CTRL", False),
    ]


def test_stop_releases_owned_input_and_invalidates_stale_generation():
    backend, executor, generation = armed()
    executor.execute({"op": "down", "x": 0, "y": 0}, generation)
    assert executor.owned
    executor.stop()
    assert backend.events[-1] == ("mouse", "left", False)
    count = len(backend.events)
    assert executor.execute({"op": "press", "key": "A"}, generation)["status"] == "rejected"
    assert len(backend.events) == count
    new_generation = executor.arm()
    assert new_generation != generation


def test_watchdog_lease_hold_and_geometry_change():
    now = [0.0]
    backend = FakeWindows()
    executor = InputExecutor(backend, clock=lambda: now[0])
    generation = executor.arm()
    executor.execute({"op": "key_down", "key": "CTRL"}, generation)
    now[0] = 1.01
    executor.watchdog()
    assert not executor.armed and not executor.owned
    executor.arm()
    backend.monitors = [Bounds(0, 0, 50, 50)]
    executor.watchdog()
    assert not executor.armed
    generation = executor.arm()
    executor.execute({"op": "down", "x": 5, "y": 5}, generation)
    now[0] += 2.1
    executor.heartbeat()
    executor.watchdog()
    assert not executor.armed and not executor.owned


def test_release_failure_blocks_rearming():
    backend, executor, generation = armed()
    executor.execute({"op": "down", "x": 0, "y": 0}, generation)
    backend.fail_release = True
    assert not executor.stop()
    assert not executor.cleanup_ok
    with pytest.raises(RuntimeError, match="cleanup"):
        executor.arm()


def test_stop_interrupts_text_without_subsequent_down_events():
    backend, executor, generation = armed()
    started = threading.Event()
    backend.on_event = started.set
    receipts = []
    thread = threading.Thread(
        target=lambda: receipts.append(
            executor.execute({"op": "text", "text": "A" * 1000}, generation)
        )
    )
    thread.start()
    assert started.wait(1)
    executor.stop("test emergency")
    count = len(backend.events)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert receipts[0]["status"] == "partial_or_unknown"
    assert len(backend.events) == count
    assert not executor.owned


def test_desktop_lock_and_human_modifiers_block_arming():
    backend = FakeWindows()
    executor = InputExecutor(backend)
    backend.held = True
    with pytest.raises(RuntimeError):
        executor.arm()
    backend.held, backend.ready = False, False
    with pytest.raises(RuntimeError):
        executor.arm()


def test_click_scroll_and_drag():
    backend, executor, generation = armed()
    for action in (
        {"op": "click", "x": 1, "y": 1, "count": 2, "button": "right"},
        {"op": "scroll", "x": 2, "y": 2, "delta": -120, "horizontal": True},
        {"op": "drag", "x": 10, "y": 10, "end_x": 30, "end_y": 30, "duration": 0.05},
    ):
        assert executor.execute(action, generation)["status"] == "inserted"
    assert ("scroll", -120, True) in backend.events
    assert ("move", 30, 30) in backend.events
    assert not executor.owned


def test_partial_native_error_disarms_without_logging_payload():
    backend, executor, generation = armed()

    def fail(unit, down):
        raise OSError("secret payload should not escape")

    backend.unicode = fail
    receipt = executor.execute({"op": "text", "text": "private text"}, generation)
    assert receipt["status"] == "partial_or_unknown"
    assert "private" not in str(receipt) and "secret" not in str(receipt)
    assert not executor.armed


def test_keyboard_focus_and_geometry_expectations_are_enforced():
    backend, executor, generation = armed()
    backend.foreground = lambda: 77
    receipt = executor.execute({"op": "text", "text": "Hi"}, generation, expected_focus=88)
    assert receipt["status"] == "rejected" and not backend.events
    generation = executor.arm()
    receipt = executor.execute(
        {"op": "text", "text": "Hi"}, generation, expected_geometry=[Bounds(0, 0, 10, 10)]
    )
    assert receipt["status"] == "rejected" and not backend.events


def test_monitor_info_layout_is_fixed_width_on_all_hosts():
    import ctypes

    from monster.computer.windows import MonitorInfo, PhysicalRect

    assert ctypes.sizeof(PhysicalRect) == 16
    assert ctypes.sizeof(MonitorInfo) == 40
    assert MonitorInfo.flags.offset == 36


@pytest.mark.parametrize("change_after", ["ALT", "F4"])
def test_window_close_chord_checks_focus_before_final_down_and_releases_after_it(change_after):
    backend, executor, generation = armed()
    focus = [77]
    backend.foreground = lambda: focus[0]

    def change_focus():
        if backend.events[-1] == ("key", change_after, True):
            focus[0] = 88

    backend.on_event = change_focus
    receipt = executor.execute(
        {"op": "chord", "keys": ["ALT", "F4"]}, generation, expected_focus=77
    )
    if change_after == "F4":
        assert receipt["status"] == "inserted"
        assert backend.events == [
            ("key", "ALT", True), ("key", "F4", True),
            ("key", "F4", False), ("key", "ALT", False),
        ]
    else:
        assert receipt["status"] == "partial_or_unknown"
        assert ("key", "F4", True) not in backend.events
    assert not executor.owned


def test_restoring_already_foreground_window_does_not_reactivate_it():
    from types import SimpleNamespace

    from monster.computer.windows import WindowsInput

    calls = []
    backend = WindowsInput.__new__(WindowsInput)
    backend.foreground = lambda: 77
    backend.api = SimpleNamespace(SetForegroundWindow=lambda handle: calls.append(handle))
    assert backend.restore_foreground(77)
    assert not calls


def test_mouse_receipt_observes_actual_cursor_without_changing_input_outcome():
    backend, executor, generation = armed()
    backend.cursor_position = lambda: (-70, 20)
    result = executor.execute({"op": "click", "x": -70, "y": 20}, generation)
    assert result["status"] == "inserted"
    assert result["cursor_physical_after"] == {"x": -70, "y": 20}
    assert backend.events == [("move", -70, 20), ("mouse", "left", True), ("mouse", "left", False)]


def test_failed_cursor_observation_does_not_reclassify_or_replay_inserted_click():
    backend, executor, generation = armed()

    def unavailable():
        raise RuntimeError("PRIVATE_NATIVE_ERROR")

    backend.cursor_position = unavailable
    result = executor.execute({"op": "click", "x": -70, "y": 20}, generation)
    assert result["status"] == "inserted"
    assert result["cursor_observation_error"]["exception_class"] == "RuntimeError"
    assert "PRIVATE_NATIVE_ERROR" not in str(result)
    assert len(backend.events) == 3
