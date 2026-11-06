import sys
from types import SimpleNamespace

import pytest

from monster.computer.contracts import Bounds
from monster.computer.screenshot import capture_display


@pytest.fixture
def fake_capture(monkeypatch):
    import monster.computer.screenshot as module

    captured = []

    class FakeMss:
        def __init__(self):
            self.monitors = [
                {"left": -10, "top": 0, "width": 20, "height": 10},
                {"left": -10, "top": 0, "width": 20, "height": 10},
            ]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def grab(self, bounds):
            captured.append(bounds)
            return SimpleNamespace(rgb=b"\x01\x02\x03", size=(bounds["width"], bounds["height"]))

    monkeypatch.setattr(
        module,
        "WindowsInput",
        lambda: SimpleNamespace(desktop_ready=lambda: True, foreground=lambda: 123),
    )
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=FakeMss))
    monkeypatch.setitem(sys.modules, "mss.tools", SimpleNamespace(to_png=lambda rgb, size: b"PNG"))
    return captured


def test_observation_physical_crop_and_metadata(fake_capture):
    observation = capture_display(1, Bounds(-8, 1, 5, 5))
    assert observation.bounds == Bounds(-8, 1, 5, 5)
    assert observation.displays == (Bounds(-10, 0, 20, 10),)
    assert observation.png == b"PNG" and len(observation.sha256) == 64
    assert fake_capture == [{"left": -8, "top": 1, "width": 5, "height": 5}]


def test_invalid_crops_and_display_do_not_capture(fake_capture):
    for crop in (Bounds(-11, 0, 2, 2), Bounds(0, 0, 100, 2), Bounds(0, 0, 0, 1)):
        with pytest.raises(ValueError):
            capture_display(1, crop)
    with pytest.raises(ValueError):
        capture_display(2)
    assert not fake_capture
