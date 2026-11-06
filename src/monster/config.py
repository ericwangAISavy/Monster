"""Non-secret GUI preferences; model/provider configuration belongs to the engine."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Preferences:
    activate: str = "Ctrl+Alt+M"
    pause: str = "Ctrl+Alt+P"
    stop: str = "Ctrl+Alt+X"
    offset_x: int = 26
    offset_y: int = 20
    reduced_motion: bool = False

    def validate(self):
        from monster.native import parse_hotkey

        keys = [parse_hotkey(value) for value in (self.activate, self.pause, self.stop)]
        if len(set(keys)) != 3:
            raise ValueError("Each shortcut must be different.")
        if not 16 <= self.offset_x <= 80 or not 12 <= self.offset_y <= 80:
            raise ValueError("Cursor offsets must stay between 16–80 and 12–80 pixels.")


def load(path: Path) -> Preferences:
    if not path.exists():
        return Preferences()
    values = json.loads(path.read_text(encoding="utf-8"))
    prefs = Preferences(**values)
    prefs.validate()
    return prefs


def save(path: Path, prefs: Preferences):
    prefs.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(prefs), indent=2), encoding="utf-8")
    temporary.replace(path)
