from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class Pill(QWidget):
    submitted = Signal(str)
    dismissed = Signal()
    answered = Signal(str, str)

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Monster · command")
        self.setAccessibleName("Monster communication pill")
        self.mode = "input"
        self.request_id = ""
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.setFixedWidth(324)
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(12, 6, 7, 6)
        self.layout_box.setSpacing(7)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.spark = QLabel("✧")
        self.spark.setFixedWidth(18)
        self.spark.setStyleSheet("color: #dfd9ff; font-size: 23px; background: transparent;")
        row.addWidget(self.spark)
        self.input = QLineEdit()
        self.input.setPlaceholderText("What should I do for you?")
        self.input.setAccessibleName("Instruction for Monster (GUI demo)")
        self.input.setMaxLength(4000)
        self.input.setFrame(False)
        self.input.setStyleSheet(
            "QLineEdit { color: #f1efff; background: transparent;"
            " font: 12px 'Segoe UI'; selection-background-color: #6159ad; }"
        )
        self.input.returnPressed.connect(self._submit)
        row.addWidget(self.input, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setStyleSheet(
            "color: #eeebff; background: transparent; font: 12px 'Segoe UI';"
        )
        row.addWidget(self.message, 1)
        self.send = QPushButton("➤")
        self.send.setAccessibleName("Send instruction")
        self.send.setToolTip("Send · Enter (GUI demo)")
        self.send.setFixedSize(32, 30)
        self.send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send.setStyleSheet(
            "QPushButton { color: white; border: 1px solid #aaa0ff;"
            " border-radius: 15px; font-size: 19px;"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 #5f65f4,stop:1 #a55cf4); }"
            "QPushButton:hover { background: #9270fb; }"
            "QPushButton:disabled { color: #aaa4cc; background: #393359; }"
        )
        self.send.clicked.connect(self._submit)
        self.input.textChanged.connect(lambda text: self.send.setEnabled(bool(text.strip())))
        row.addWidget(self.send)
        self.layout_box.addLayout(row)
        self.buttons = QWidget()
        controls = QHBoxLayout(self.buttons)
        controls.setContentsMargins(26, 0, 0, 2)
        controls.setSpacing(8)
        self.reject = QPushButton("Decline")
        self.approve = QPushButton("Approve")
        for button in (self.reject, self.approve):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { color: #eeeaff; background: #343057;"
                "border: 1px solid #7770ba; border-radius: 9px; padding: 5px 12px; }"
                "QPushButton:hover { background: #51477c; }"
            )
        self.reject.clicked.connect(lambda: self._answer("reject"))
        self.approve.clicked.connect(lambda: self._answer("approve"))
        controls.addStretch()
        controls.addWidget(self.reject)
        controls.addWidget(self.approve)
        self.layout_box.addWidget(self.buttons)
        self.show_input()
        self.hide()

    def _reset(self, mode: str):
        self._hide_timer.stop()
        self.mode = mode
        self.request_id = ""
        self.input.setVisible(mode in ("input", "clarification"))
        self.send.setVisible(mode in ("input", "clarification"))
        self.message.setVisible(mode in ("status", "approval"))
        self.buttons.setVisible(mode == "approval")
        self.setFixedHeight(46 if mode != "approval" else 112)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, mode == "status")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, mode == "status")
        # Passive notices must not block the desktop beneath them.
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, mode == "status")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, mode == "status")

    def show_input(self):
        self._reset("input")
        self.input.setPlaceholderText("What should I do for you?")
        self.input.setToolTip("GUI demo · no model or computer control is connected")
        self.send.setEnabled(bool(self.input.text().strip()))

    def show_status(self, text: str, duration_ms: int = 2600):
        self._reset("status")
        self.message.setText(text)
        # A brief status can wrap to two lines, but never grows into a chat window.
        self.setFixedHeight(58 if len(text) > 44 else 46)
        self.show()
        self._hide_timer.start(duration_ms)

    def show_question(self, request_id: str, text: str, approval: bool):
        self._reset("approval" if approval else "clarification")
        self.request_id = request_id
        if approval:
            self.approve.setEnabled(len(text) <= 400)
            if len(text) > 400:
                text = "Action details exceed this compact view. Decline and review manually."
            self.message.setText(text)
            metrics = self.message.fontMetrics().boundingRect(
                QRect(0, 0, 258, 1000), Qt.TextFlag.TextWordWrap, text
            )
            self.setFixedHeight(max(112, min(280, metrics.height() + 66)))
            # Do not bind Enter to Approve. Explicit click/tab navigation is required.
            self.reject.setFocus()
        else:
            self.input.clear()
            self.input.setPlaceholderText(text)
            self.input.setToolTip(text)
        self.show()

    def _submit(self):
        text = self.input.text().strip()
        if not text or self.mode not in ("input", "clarification"):
            return
        if self.mode == "clarification":
            self.answered.emit(self.request_id, text)
        else:
            self.submitted.emit(text)

    def _answer(self, answer: str):
        if self.mode == "approval" and (answer != "approve" or self.approve.isEnabled()):
            self.answered.emit(self.request_id, answer)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.dismissed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(127, 112, 255, 45), 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 22, 22)
        fill = QLinearGradient(0, 0, 0, self.height())
        fill.setColorAt(0, QColor(24, 29, 69, 244))
        fill.setColorAt(1, QColor(15, 19, 46, 240))
        p.setBrush(fill)
        p.setPen(QPen(QColor("#a69bff"), 1.1))
        p.drawRoundedRect(r, 22, 22)
