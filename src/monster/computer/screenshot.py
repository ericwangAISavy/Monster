"""Physical-pixel PNG capture. Call through the GUI isolation gate when it is present."""

import hashlib
import time
import uuid
from dataclasses import dataclass

from monster.computer.contracts import Bounds, integer
from monster.computer.windows import WindowsInput


@dataclass(frozen=True)
class Observation:
    id: str
    captured_at: float
    bounds: Bounds
    displays: tuple[Bounds, ...]
    png: bytes
    sha256: str
    foreground: int = 0


def capture_display(display: int = 1, crop: Bounds | None = None) -> Observation:
    from mss import mss
    from mss.tools import to_png

    windows = WindowsInput()
    if not windows.desktop_ready():
        raise RuntimeError("An unlocked standard Windows desktop is required for capture.")
    with mss() as backend:
        monitors = backend.monitors
        integer(display, 0, len(monitors) - 1)  # 0 explicitly requests full virtual desktop.
        displays = tuple(Bounds(m["left"], m["top"], m["width"], m["height"]) for m in monitors[1:])
        monitor = monitors[display]
        bounds = Bounds(monitor["left"], monitor["top"], monitor["width"], monitor["height"])
        if crop is not None:
            for value in (crop.x, crop.y, crop.width, crop.height):
                integer(value, -100000, 100000)
            if (
                crop.width <= 0
                or crop.height <= 0
                or not bounds.contains(crop.x, crop.y)
                or not bounds.contains(crop.x + crop.width - 1, crop.y + crop.height - 1)
            ):
                raise ValueError("Crop must be a positive physical rectangle inside the capture.")
            bounds = crop
        if bounds.width * bounds.height > 40_000_000:
            raise ValueError("Capture exceeds the 40 megapixel limit; select a display or crop.")
        started = time.monotonic()
        foreground = windows.foreground()
        shot = backend.grab(
            {"left": bounds.x, "top": bounds.y, "width": bounds.width, "height": bounds.height}
        )
        if not any(shot.rgb):
            raise RuntimeError("Capture is black/unavailable; inspect the desktop manually.")
        png = to_png(shot.rgb, shot.size)
    if len(png) > 32 * 1024 * 1024:
        raise ValueError("Encoded screenshot exceeds the 32 MiB limit.")
    return Observation(
        uuid.uuid4().hex,
        started,
        bounds,
        displays,
        png,
        hashlib.sha256(png).hexdigest(),
        foreground,
    )
