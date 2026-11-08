"""Persistent right-edge control surface backed by the application's real actions."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

PANEL_STYLE = """
QWidget#monsterPanel { background: #222626; border: 1px solid #3b4040; border-radius: 16px; }
QWidget { color: #f2f4f3; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QLabel#brand { font-size: 25px; font-weight: 800; letter-spacing: -1px; }
QLabel#muted { color: #a2adab; font-size: 12px; }
QLabel#status { color: #a7e4d4; background: #303a37; border-radius: 8px; padding: 9px; }
QLabel#sectionTitle { font-size: 14px; font-weight: 650; }
QFrame#section { border-top: 1px solid #3a3e3e; }
QPushButton, QToolButton { background: #303535; border: 1px solid transparent;
    border-radius: 8px; padding: 10px 12px; text-align: left; }
QPushButton:hover, QToolButton:hover { background: #3b4140; border-color: #565e5b; }
QPushButton:focus, QToolButton:focus { border-color: #aa98ff; }
QPushButton:disabled { color: #78817f; }
QPushButton#primary { background: #a793fa; color: #191726; font-weight: 700; }
QPushButton#primary:hover { background: #b9a8ff; }
QPushButton#danger { color: #ffb4b9; background: #453033; }
QPushButton:checked { background: #51476c; border-color: #a793fa; }
QPushButton#iconButton { padding: 7px; text-align: center; background: transparent; }
QScrollArea, QStackedWidget, QWidget#page { background: transparent; border: none; }
QScrollBar:vertical { width: 6px; background: transparent; margin: 4px 0; }
QScrollBar::handle:vertical { background: #58605e; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class PanelSwitch(QCheckBox):
    """Keyboard-accessible checkbox with a right-aligned switch indicator."""

    def sizeHint(self):
        return QSize(220, 38)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor('#f2f4f3' if self.isEnabled() else '#78817f'))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width() - 62)
        painter.drawText(self.rect().adjusted(0, 0, -60, 0), Qt.AlignmentFlag.AlignVCenter, text)
        track = QRectF(self.width() - 44, (self.height() - 24) / 2, 44, 24)
        painter.setPen(QPen(QColor('#78817f'), 1) if not self.isChecked() else Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#a793fa' if self.isChecked() else '#303535'))
        painter.drawRoundedRect(track, 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#222626' if self.isChecked() else '#dde2e0'))
        painter.drawEllipse(QRectF(track.x() + (24 if self.isChecked() else 4), track.y() + 4, 16, 16))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#a793fa'), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)


class ControlPanel(QWidget):
    """Stays open on focus loss; close/Escape hides it without quitting Monster."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName('monsterPanel')
        self.setWindowTitle('Monster · control center')
        self.setAccessibleName('Monster control center')
        self.setStyleSheet(PANEL_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(448, 780)
        self._screen = None
        self.detail_pages = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(16)
        header = QHBoxLayout()
        brand = QLabel('monster')
        brand.setObjectName('brand')
        header.addWidget(brand)
        header.addStretch()
        self.close_button = QPushButton('✕')
        self.close_button.setObjectName('iconButton')
        self.close_button.setAccessibleName('Hide control center')
        self.close_button.setToolTip('Hide · Monster keeps running')
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button)
        root.addLayout(header)
        self.status = QLabel('●  Ready when you are')
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.navigation = QPushButton('‹  Control center')
        self.navigation.clicked.connect(self.home)
        self.navigation.hide()
        root.addWidget(self.navigation)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.dashboard = QWidget()
        self.dashboard.setObjectName('page')
        self.content = QVBoxLayout(self.dashboard)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.dashboard)
        self.stack.addWidget(scroll)
        self.home_page = scroll
        footer = QLabel('Your desktop companion   ·   Ctrl+Alt+X to stop')
        footer.setObjectName('muted')
        footer.setWordWrap(True)
        root.addWidget(footer)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#3b4040'), 1))
        painter.setBrush(QColor('#222626'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 16, 16)

    def section(self, title, description='', collapsed=False):
        frame = QFrame()
        frame.setObjectName('section')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(8)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        if collapsed:
            heading = QToolButton()
            heading.setText(title.replace('&', '&&'))
            heading.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            heading.setCheckable(True)
            heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            heading.setArrowType(Qt.ArrowType.RightArrow)
            heading.toggled.connect(body.setVisible)
            heading.toggled.connect(lambda on: heading.setArrowType(
                Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow))
            body.hide()
        else:
            heading = QLabel(title)
            heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        if description:
            hint = QLabel(description)
            hint.setObjectName('muted')
            hint.setWordWrap(True)
            body_layout.addWidget(hint)
        layout.addWidget(body)
        self.content.addWidget(frame)
        return body_layout

    def action_button(self, layout, action, label=None, kind=None):
        text = label or action.text().replace('…', '')
        button = PanelSwitch(text) if action.isCheckable() else QPushButton(text.replace('&', '&&'))
        if kind:
            button.setObjectName(kind)
        button.setCheckable(action.isCheckable())
        button.setChecked(action.isChecked())
        button.setEnabled(action.isEnabled())
        button.setVisible(action.isVisible())

        def sync():
            button.setEnabled(action.isEnabled())
            button.setVisible(action.isVisible())
            button.setChecked(action.isChecked())
            if label is None:
                button.setText(action.text().replace('…', '').replace('&', '&&'))

        action.changed.connect(sync)
        button.clicked.connect(lambda: action.trigger() if action.isCheckable() else self.invoke(action))
        layout.addWidget(button)
        return button

    def invoke(self, action):
        # Restore the task desktop before opening input or any diagnostic capture.
        self.hide()
        action.trigger()

    def present(self, widget):
        page = self.detail_pages.get(widget)
        if page is None:
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            widget.setWindowFlags(Qt.WindowType.Widget)
            widget.setMinimumWidth(0)
            for form in widget.findChildren(QFormLayout):
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            page.setWidget(widget)
            self.stack.addWidget(page)
            self.detail_pages[widget] = page
            widget.finished.connect(lambda _: self.home())

            def discard():
                self.detail_pages.pop(widget, None)
                if isValid(page):
                    page.deleteLater()

            widget.destroyed.connect(discard)
        self.stack.setCurrentWidget(page)
        widget.show()
        self.navigation.show()
        self.open()

    def home(self):
        self.stack.setCurrentWidget(self.home_page)
        self.navigation.hide()

    def open(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if self._screen is not screen:
            if self._screen is not None and isValid(self._screen):
                self._screen.availableGeometryChanged.disconnect(self.dock)
            self._screen = screen
            screen.availableGeometryChanged.connect(self.dock)
        self.dock()
        self.show()
        self.raise_()
        self.activateWindow()

    def dock(self, *_):
        if self._screen is None or not isValid(self._screen):
            return
        area = self._screen.availableGeometry()
        width, height = min(448, area.width()), min(860, area.height() - 24)
        self.resize(width, max(1, height))
        self.move(area.right() - width - 11 if area.width() > width + 24 else area.left(),
                  area.top() + 12 if area.height() > height else area.top())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.hide()
        event.ignore()
