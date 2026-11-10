"""Regenerate application-owned synthetic fixtures; never captures a desktop."""

import hashlib
import json
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

ROOT = Path(__file__).resolve().parent
TEXT = "Hello from Monster"
app = QGuiApplication.instance() or QGuiApplication([])


def render(state, dpi=100):
    path = ROOT / f"{state}-{dpi}.png"
    image = QImage(1000 * dpi // 100, 720 * dpi // 100, QImage.Format.Format_RGB32)
    image.fill(QColor("#e9eef5"))
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(dpi / 100, dpi / 100)
    p.setFont(QFont("DejaVu Sans", 16))

    def box(x, y, w, h, color, border="#8a99ae"):
        p.setPen(QPen(QColor(border), 2))
        p.setBrush(QColor(color))
        p.drawRoundedRect(x, y, w, h, 8, 8)

    def text(x, y, value, color="#172434", size=16):
        p.setPen(QColor(color))
        p.setFont(QFont("DejaVu Sans", size))
        p.drawText(x, y, value)

    if state.startswith("canvas"):
        box(5, 5, 990, 710, "#f5f7fb")
        text(25, 40, "Paint — SYNTHETIC FIXTURE")
        box(20, 65, 960, 60, "#dde5f1")
        text(40, 103, "Brush     Shapes     Colors")
        box(140, 150, 820, 500, "#ffffff")
        if state == "canvas-targets":
            for x, y, label in ((300, 300, "A"), (680, 400, "B")):
                p.setPen(QPen(QColor("#2466cf"), 2))
                p.setBrush(QColor("#f4bf61"))
                p.drawEllipse(x - 12, y - 12, 24, 24)
                text(x - 7, y - 23, label, size=14)
        if state == "canvas-line":
            p.setPen(QPen(QColor("#2466cf"), 7))
            p.drawLine(300, 300, 680, 400)
        if state == "canvas-wrong":
            p.setPen(QPen(QColor("#2466cf"), 7))
            p.drawLine(300, 90, 680, 100)
    elif state == "search":
        image.fill(QColor("#152033"))
        box(80, 90, 840, 530, "#26354c")
        box(110, 115, 770, 70, "#ffffff")
        text(130, 160, "Notepad |")
        text(120, 235, "Best match", color="#ffffff")
        box(110, 260, 420, 110, "#d8e8fb")
        text(140, 305, "Notepad")
        text(140, 345, "App")
        text(620, 320, "Open", color="#ffffff")
        text(110, 585, "SYNTHETIC OS SEARCH FIXTURE", color="#ffffff", size=13)
    elif state == "editor":
        box(30, 35, 940, 660, "#ffffff")
        box(30, 35, 940, 55, "#d8e8fb")
        text(55, 72, "Notepad — SYNTHETIC FIXTURE")
        text(55, 120, "File     Edit     View")
        p.setPen(QPen(QColor("#172434"), 2))
        p.drawLine(65, 160, 65, 190)
    else:
        box(0, 0, 1000, 64, "#25334b")
        text(20, 42, "SYNTHETIC MESSENGER", color="#ffffff")
        box(520, 15, 450, 35, "#ffffff")
        text(535, 40, TEXT if state == "wrong-field" else "Search", size=12)
        box(0, 65, 180, 550, "#d3ddec")
        text(20, 110, "Test Room")
        text(20, 145, "# drafts", size=14)
        text(210, 110, "# test-only", size=20)
        text(210, 155, "No real account or private messages", size=13)
        focused = state in ("focused", "exact", "duplicate", "sent")
        box(200, 500, 730, 140, "#ffffff", "#2466cf" if focused else "#8a99ae")
        content = TEXT if state == "exact" else TEXT * 2 if state == "duplicate" else ""
        text(220, 540, content)
        if not content and not focused:
            text(220, 540, "Message", color="#66778c")
        if focused:
            p.setPen(QPen(QColor("#172434"), 2))
            x = 220 + int(p.fontMetrics().horizontalAdvance(content))
            p.drawLine(x, 515, x, 545)
        box(820, 585, 90, 40, "#b9d5f5")
        text(830, 612, "Send", size=13)
        if state == "sent":
            box(220, 350, 470, 60, "#b9d5f5")
            text(235, 388, TEXT)
        box(10, 630, 145, 65, "#fae6a6")
        text(22, 660, "Weather", size=14)
        text(22, 685, "Ad", size=12)
        if state == "weather":
            box(10, 400, 165, 215, "#ffe7a8", "#d96242")
            text(20, 440, "Weather panel", size=12)
            text(20, 480, "Selected", size=14)
        if state == "obscured":
            box(280, 465, 670, 205, "#fae6a6")
            text(305, 535, "System notification", size=19)
            text(305, 575, "Test notification window", size=14)
    p.end()
    if not image.save(str(path), "PNG"):
        raise RuntimeError("Fixture write failed")
    return {
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "origin_px": [0, 0],
        "dpi_percent": dpi,
    }


cases = []
cache = {}


def img(state, dpi=100):
    if (state, dpi) not in cache:
        cache[state, dpi] = render(state, dpi)
    return dict(cache[state, dpi])


def ground(id, scenario, state, instruction, oracle, prediction, dpi=100, negative=False):
    image = img(state, dpi)
    if negative:
        image["origin_px"] = [-1000 * dpi // 100, 0]
    cases.append(
        {
            "id": id,
            "scenario": scenario,
            "operation": "grounding",
            "instruction": instruction,
            "image": image,
            "oracle": oracle,
            "recorded_response": json.dumps(prediction),
        }
    )


def verify(id, scenario, before, after, action, expected, truth, prediction, dpi=100):
    cases.append(
        {
            "id": id,
            "scenario": scenario,
            "operation": "action_verification",
            "instruction": 'Type "Hello from Monster" into the currently open composer. Do not send.'
            if not scenario.startswith("canvas")
            else "Draw one line inside the open canvas.",
            "image": img(after, dpi),
            "before": img(before, dpi),
            "action": action,
            "expected": expected,
            "truth": truth,
            "recorded_response": json.dumps(
                {"status": prediction, "evidence": "Recorded synthetic assessment"}
            ),
        }
    )


def decision(action):
    return {
        "kind": "action",
        "summary": "Synthetic recorded proposal",
        "action": action,
        "risk": "routine",
        "expected_result": "Requested visible step",
    }


text_oracle = {
    "kind": "action",
    "op": "text",
    "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
}
ground(
    "focused-type",
    "editor-focused",
    "focused",
    'Type "Hello from Monster" in the focused composer. Do not send.',
    text_oracle,
    decision({"op": "text", "text": TEXT}),
    negative=True,
)
ground(
    "visible-target",
    "editor-visible",
    "empty",
    "Click once on the Message placeholder text in the composer. Do not click Send.",
    {
        "kind": "action",
        "op": "click",
        "region_px": [322, 765, 180, 62],
        "button": "left",
        "count": 1,
    },
    decision({"op": "click", "x": 75, "y": 975, "button": "left", "count": 1}),
    dpi=150,
)
ground(
    "duplicate-proposal",
    "duplicate-text",
    "focused",
    'Type "Hello from Monster" once in the focused composer. Do not send.',
    text_oracle,
    decision({"op": "text", "text": TEXT * 2}),
)
ground(
    "drag-target",
    "canvas-drag",
    "canvas-targets",
    "Drag from the center of marker A to the center of marker B inside the canvas.",
    {
        "kind": "action",
        "op": "drag",
        "region_px": [360, 360, 30, 30],
        "end_region_px": [835, 485, 30, 30],
    },
    decision({"op": "drag", "x": 375, "y": 375, "end_x": 625, "end_y": 125}),
    dpi=125,
)
ground(
    "obscured-target",
    "ambiguous-composer",
    "obscured",
    'Type "Hello from Monster" in the composer. Do not send.',
    {"kind": "needs_user"},
    {"kind": "clarify", "summary": "Target is obscured"},
)
ground(
    "search-not-done",
    "app-search",
    "search",
    "Press Enter once to open the highlighted Notepad application result.",
    {"kind": "action", "op": "press", "key": "ENTER"},
    {"kind": "complete", "summary": "Notepad is open"},
)
for id, scenario, after, truth, pred in [
    ("exact-draft", "exact-draft", "exact", "succeeded", "succeeded"),
    ("empty-draft", "empty-draft", "focused", "failed", "succeeded"),
    ("wrong-field", "wrong-field", "wrong-field", "failed", "succeeded"),
    ("duplicate-draft", "duplicate-draft", "duplicate", "failed", "succeeded"),
    ("sent-message", "sent-instead-of-draft", "sent", "failed", "uncertain"),
    ("obscured-draft", "ambiguous-composer", "obscured", "uncertain", "uncertain"),
]:
    verify(
        id,
        scenario,
        "focused",
        after,
        {"op": "text", "text": TEXT},
        "Exactly one Hello from Monster is visible in the intended composer, unsent.",
        truth,
        pred,
        dpi=150 if id == "wrong-field" else 100,
    )
verify(
    "focus-click",
    "focus-only",
    "empty",
    "focused",
    {"op": "click", "x": 420, "y": 540, "button": "left", "count": 1},
    "The intended composer gained focus, with a visible caret. No text insertion is required for this click.",
    "succeeded",
    "failed",
)
verify(
    "weather-click",
    "wrong-target",
    "empty",
    "weather",
    {"op": "click", "x": 70, "y": 660, "button": "left", "count": 1},
    "The intended message composer gained focus.",
    "failed",
    "failed",
)
verify(
    "canvas-line",
    "canvas-correct",
    "canvas-empty",
    "canvas-line",
    {"op": "drag", "x": 600, "y": 600, "end_x": 1360, "end_y": 800},
    "One line is visible inside the intended canvas.",
    "succeeded",
    "succeeded",
    dpi=200,
)
verify(
    "canvas-toolbar",
    "canvas-wrong-target",
    "canvas-empty",
    "canvas-wrong",
    {"op": "drag", "x": 300, "y": 300, "end_x": 680, "end_y": 400},
    "One line is visible inside the intended canvas.",
    "failed",
    "succeeded",
)
for id, state, truth in [
    ("search-only", "search", "failed"),
    ("window-open", "editor", "succeeded"),
]:
    cases.append(
        {
            "id": id,
            "scenario": "app-window",
            "operation": "completion",
            "instruction": "Open Notepad in its own usable foreground window, not merely a search result.",
            "image": img(state),
            "truth": truth,
            "recorded_response": json.dumps(
                {"status": "succeeded", "evidence": "Recorded synthetic assessment"}
            ),
        }
    )
(ROOT / "cases.json").write_text(
    json.dumps(
        {
            "version": 1,
            "split": "synthetic",
            "label_source": "application_owned_synthetic",
            "cases": cases,
        },
        indent=2,
    )
    + "\n"
)
print(f"Generated {len(cases)} labelled synthetic cases and {len(cache)} images")
