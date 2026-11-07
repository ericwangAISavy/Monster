from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from monster.config import Preferences


class Settings(QDialog):
    saved = Signal(object)

    def __init__(self, preferences: Preferences):
        super().__init__()
        self.setWindowTitle("Monster · companion settings")
        self.setMinimumWidth(380)
        self.setStyleSheet(
            "QDialog { background: #12172c; color: #eeeaff; }"
            "QLabel, QCheckBox { color: #eeeaff; }"
            "QLineEdit, QSpinBox { background: #242b46; color: white;"
            "border: 1px solid #615c91; border-radius: 5px; padding: 6px; }"
        )
        layout = QVBoxLayout(self)
        info = QLabel(
            "Companion appearance and shortcut settings.\n"
            "Choose demo or Ollama preview in AI model settings."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.fields = {}
        for name, label in (
            ("activate", "Activate"),
            ("pause", "Pause / resume"),
            ("stop", "Emergency stop"),
        ):
            field = QLineEdit(getattr(preferences, name))
            form.addRow(label, field)
            self.fields[name] = field
        for name, label, minimum in (
            ("offset_x", "Horizontal offset", 16),
            ("offset_y", "Vertical offset", 12),
        ):
            field = QSpinBox()
            field.setRange(minimum, 80)
            field.setValue(getattr(preferences, name))
            form.addRow(label, field)
            self.fields[name] = field
        self.reduced = QCheckBox("Reduce character motion")
        self.reduced.setChecked(preferences.reduced_motion)
        form.addRow(self.reduced)
        self.companion_scale = QSpinBox()
        self.companion_scale.setRange(75, 150)
        self.companion_scale.setSuffix("%")
        self.companion_scale.setValue(preferences.companion_scale)
        form.addRow("Companion size", self.companion_scale)
        self.companion_position = QComboBox()
        self.companion_position.addItem("Follow cursor", "cursor")
        self.companion_position.addItem("Top right · primary display", "top_right")
        self.companion_position.addItem("Bottom right · primary display", "bottom_right")
        self.companion_position.setCurrentIndex(
            self.companion_position.findData(preferences.companion_position)
        )
        form.addRow("Companion position", self.companion_position)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ffb7bf;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self):
        prefs = Preferences(
            **{
                name: field.text().strip()
                for name, field in self.fields.items()
                if isinstance(field, QLineEdit)
            },
            **{
                name: field.value()
                for name, field in self.fields.items()
                if isinstance(field, QSpinBox)
            },
            reduced_motion=self.reduced.isChecked(),
            companion_scale=self.companion_scale.value(),
            companion_position=self.companion_position.currentData(),
        )
        try:
            prefs.validate()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.saved.emit(prefs)
