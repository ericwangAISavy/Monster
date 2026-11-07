"""Bounded minimum-jerk paths in physical pixels, with no native input calls."""

import math

from monster.computer.contracts import integer


def motion_plan(start, end, displays, duration=None):
    for point in (start, end):
        if type(point) not in (tuple, list) or len(point) != 2:
            raise ValueError('Physical cursor position unavailable.')
        for value in point:
            integer(value, -100000, 100000)
        if not any(rect.contains(*point) for rect in displays):
            raise ValueError('Pointer path endpoint is outside displays.')
    dx, dy = end[0] - start[0], end[1] - start[1]
    span = max(abs(dx), abs(dy))
    # Check the whole line before moving or pressing a button, including thin gaps.
    for index in range(span + 1):
        t = index / max(1, span)
        x, y = round(start[0] + dx * t), round(start[1] + dy * t)
        if not any(rect.contains(x, y) for rect in displays):
            raise ValueError('Pointer path crosses a monitor gap.')
    if not span:
        return ((0.0, tuple(end)),)
    if duration is None:
        duration = min(.8, .18 + math.hypot(dx, dy) / 2000)
    if type(duration) not in (int, float) or not .05 <= duration <= 2:
        raise ValueError('Pointer duration is outside its bounded range.')
    steps = max(2, math.ceil(duration / .016))
    result = []
    for index in range(1, steps + 1):
        t = index / steps
        eased = t * t * t * (10 + t * (-15 + 6 * t))
        point = (round(start[0] + dx * eased), round(start[1] + dy * eased))
        if index == steps:
            point = tuple(end)
        if not any(rect.contains(*point) for rect in displays):
            raise ValueError('Pointer path crosses a monitor gap.')
        result.append((duration * t, point))
    return tuple(result)
