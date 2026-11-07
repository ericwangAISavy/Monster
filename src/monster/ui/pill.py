from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
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
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setStyleSheet(
            "color: #eeebff; background: transparent; font: 12px 'Segoe UI';"
        )
        self.message_area = QScrollArea()
        self.message_area.setWidgetResizable(True)
        self.message_area.setFrameShape(QFrame.Shape.NoFrame)
        self.message_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.message_area.viewport().setAutoFillBackground(False)
        self.message_area.setWidget(self.message)
        self.message_area.setMinimumSize(0, 0)
        row.addWidget(self.message_area, 1)
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
        self.effect_controls = QWidget()
        extra = QHBoxLayout(self.effect_controls)
        extra.setContentsMargins(26, 0, 0, 2)
        self.edit_effect = QPushButton("Edit / prepare again")
        self.pause_effect = QPushButton("Pause mission")
        self.edit_effect.clicked.connect(lambda: self._answer("edit"))
        self.pause_effect.clicked.connect(lambda: self._answer("pause"))
        extra.addWidget(self.edit_effect)
        extra.addWidget(self.pause_effect)
        self.layout_box.addWidget(self.effect_controls)
        self.show_input()
        self.hide()

    def _reset(self, mode: str, *, passive=False):
        self._hide_timer.stop()
        self.mode = mode
        self.request_id = ""
        self.effect_controls.hide()
        self.approve.setText("Approve")
        self.reject.setText("Decline")
        self.edit_effect.setText("Edit / prepare again")
        self.pause_effect.setText("Pause mission")
        self.input.setVisible(mode in ("input", "clarification"))
        self.send.setVisible(mode in ("input", "clarification"))
        self.message_area.setVisible(mode in ("status", "approval"))
        self.message.setMinimumHeight(0)
        self.buttons.setVisible(mode == "approval")
        if mode in ("input", "clarification"):
            self.setFixedHeight(46)
        passive = passive or mode == "status"
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, passive)
        # Passive notices must not block the desktop beneath them.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, mode == "status")
        # One final flag change avoids recreating the native window through an
        # interactive intermediate state on every keyboard approval prompt.
        flags = self.windowFlags() & ~(
            Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput
        )
        if passive:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        if mode == "status":
            flags |= Qt.WindowType.WindowTransparentForInput
        if flags != self.windowFlags():
            self.setWindowFlags(flags)

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

    def show_question(self, request_id: str, text: str, approval: bool, *, take_focus=True):
        self._reset("approval" if approval else "clarification", passive=approval and not take_focus)
        self.request_id = request_id
        if approval:
            self.approve.setEnabled(len(text) <= 3500)
            if len(text) > 3500:
                text = "Action details exceed this compact view. Decline and review manually."
            self.message.setText(text)
            metrics = self.message.fontMetrics().boundingRect(
                QRect(0, 0, 244, 10000), Qt.TextFlag.TextWordWrap, text
            )
            self.message.setMinimumHeight(metrics.height())
            self.setFixedHeight(max(112, min(280, metrics.height() + 66)))
            # Plain Enter never approves; approval requires a deliberate gesture.
            if take_focus:
                self.reject.setFocus()
        else:
            self.input.clear()
            self.input.setPlaceholderText(text)
            self.input.setToolTip(text)
        self.show()

    def show_effect_controls(self):
        self.approve.setText("Approve this exact action")
        self.reject.setText("Reject")
        self.effect_controls.show()
        self.setFixedHeight(self.height() + 40)

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
