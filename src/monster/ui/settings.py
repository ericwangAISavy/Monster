from PySide6.QtCore import QSize, Qt, Signal
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

from monster.characters import CHARACTERS
from monster.config import Preferences
from monster.ui.character_picker import CharacterPreview, character_icon


class Settings(QDialog):
    saved = Signal(object)

    def __init__(self, preferences: Preferences):
        super().__init__()
        self.setWindowTitle("Monster · companion settings")
        self.setMinimumWidth(380)
        self.setStyleSheet(
            "QDialog { background: #222626; color: #f2f4f3; }"
            "QLabel, QCheckBox { color: #f2f4f3; }"
            "QLineEdit, QSpinBox, QComboBox { background: #303535; color: white;"
            "border: 1px solid #505956; border-radius: 5px; padding: 6px; }"
        )
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.companion_character = QComboBox()
        self.companion_character.setAccessibleName("Companion character")
        self.companion_character.setIconSize(QSize(40, 40))
        for key, style in CHARACTERS.items():
            self.companion_character.addItem(character_icon(key), style.label, key)
        self.companion_character.setCurrentIndex(
            self.companion_character.findData(preferences.companion_character))
        form.addRow("Character", self.companion_character)
        self.character_preview = CharacterPreview(preferences.companion_character,
                                                   preferences.reduced_motion)
        self.companion_character.currentIndexChanged.connect(
            lambda _: self.character_preview.set_character(self.companion_character.currentData()))
        form.addRow(self.character_preview)
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
        self.reduced.toggled.connect(self.character_preview.set_reduced)
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
            companion_character=self.companion_character.currentData(),
            companion_position=self.companion_position.currentData(),
        )
        try:
            prefs.validate()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.saved.emit(prefs)
